package main

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// splitroute-probe: unprivileged (or cap_net_raw) read-only HTTPS probe & speed test worker
// that can pin sockets to a specific network interface using SO_BINDTODEVICE.

func boundDialContext(iface string) func(ctx context.Context, network, addr string) (net.Conn, error) {
	return func(ctx context.Context, network, addr string) (net.Conn, error) {
		d := net.Dialer{
			Timeout: 6 * time.Second,
			Control: func(network, address string, c syscall.RawConn) error {
				if iface == "" {
					return nil
				}
				var bindErr error
				err := c.Control(func(fd uintptr) {
					bindErr = syscall.SetsockoptString(int(fd), syscall.SOL_SOCKET, syscall.SO_BINDTODEVICE, iface)
				})
				if err != nil {
					return err
				}
				return bindErr
			},
		}
		return d.DialContext(ctx, network, addr)
	}
}

func newHTTPClient(iface string, forceFamily string, timeout time.Duration) *http.Client {
	dial := boundDialContext(iface)
	tr := &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			if forceFamily == "ip4" {
				network = "tcp4"
			} else if forceFamily == "ip6" {
				network = "tcp6"
			}
			return dial(ctx, network, addr)
		},
		DisableKeepAlives: true,
	}
	return &http.Client{Transport: tr, Timeout: timeout}
}

type ipProbeOut struct {
	IP        string  `json:"ip,omitempty"`
	LatencyMs float64 `json:"latencyMs,omitempty"`
	Error     string  `json:"error,omitempty"`
}

func cmdPublicIP(iface string, v6 bool) {
	host := "https://api.ipify.org/"
	fam := "ip4"
	if v6 {
		host = "https://api6.ipify.org/"
		fam = "ip6"
	}
	client := newHTTPClient(iface, fam, 7*time.Second)
	start := time.Now()
	req, _ := http.NewRequest("GET", host, nil)
	req.Header.Set("User-Agent", "SplitRoute/1.0.0")
	resp, err := client.Do(req)
	var out ipProbeOut
	if err != nil {
		out.Error = friendlyErr(err)
	} else {
		defer resp.Body.Close()
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 256))
		ms := float64(time.Since(start).Microseconds()) / 1000.0
		ip := strings.TrimSpace(string(body))
		if resp.StatusCode == 200 && net.ParseIP(ip) != nil {
			out.IP = ip
			out.LatencyMs = ms
		} else {
			out.Error = fmt.Sprintf("HTTP %d", resp.StatusCode)
		}
	}
	enc, _ := json.Marshal(out)
	fmt.Println(string(enc))
}

func friendlyErr(err error) string {
	s := err.Error()
	switch {
	case strings.Contains(s, "operation not permitted"):
		return "Install the background service to pin probes to this interface"
	case strings.Contains(s, "network is unreachable"), strings.Contains(s, "no such device"):
		return "No route to the internet from this interface"
	case strings.Contains(s, "connection refused"):
		return "Connection refused"
	case strings.Contains(s, "timeout"), strings.Contains(s, "deadline exceeded"):
		return "No internet through this path (timed out)"
	default:
		return s
	}
}

func cmdPing(iface, url string) {
	client := newHTTPClient(iface, "", 4*time.Second)
	start := time.Now()
	req, _ := http.NewRequest("GET", url, nil)
	req.Header.Set("User-Agent", "SplitRoute/1.0.0")
	resp, err := client.Do(req)
	if err != nil {
		fmt.Printf(`{"error":%q}`+"\n", friendlyErr(err))
		return
	}
	_, _ = io.ReadAll(io.LimitReader(resp.Body, 4096))
	resp.Body.Close()
	ms := float64(time.Since(start).Microseconds()) / 1000.0
	fmt.Printf(`{"ms":%.2f}`+"\n", ms)
}

type speedEvent struct {
	Type     string  `json:"type"` // sample | done | error
	Mbps     float64 `json:"mbps"`
	Bytes    int64   `json:"bytes"`
	LoadedMs float64 `json:"loadedPingMs,omitempty"`
	Progress float64 `json:"progress"`
	Error    string  `json:"error,omitempty"`
}

func emit(ev speedEvent) {
	b, _ := json.Marshal(ev)
	fmt.Println(string(b))
}

func cmdTransfer(mode, iface, url, pingURL string, seconds float64, streams int) {
	var totalBytes atomic.Int64
	var errMu sync.Mutex
	var firstErr string
	setErr := func(e error) {
		if e == nil {
			return
		}
		errMu.Lock()
		if firstErr == "" {
			firstErr = friendlyErr(e)
		}
		errMu.Unlock()
	}

	deadline := time.Now().Add(time.Duration(seconds * float64(time.Second)))
	ctx, cancel := context.WithDeadline(context.Background(), deadline.Add(2*time.Second))
	defer cancel()

	var wg sync.WaitGroup
	payload := make([]byte, 10*1024*1024)
	if mode == "upload" {
		_, _ = rand.Read(payload[:65536])
	}

	for s := 0; s < streams; s++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			client := newHTTPClient(iface, "", time.Duration(seconds+4)*time.Second)
			for time.Now().Before(deadline) {
				if mode == "download" {
					req, _ := http.NewRequestWithContext(ctx, "GET", url, nil)
					req.Header.Set("User-Agent", "SplitRoute/1.0.0")
					resp, err := client.Do(req)
					if err != nil {
						setErr(err)
						time.Sleep(250 * time.Millisecond)
						continue
					}
					buf := make([]byte, 64*1024)
					for time.Now().Before(deadline) {
						n, rerr := resp.Body.Read(buf)
						if n > 0 {
							totalBytes.Add(int64(n))
						}
						if rerr != nil {
							break
						}
					}
					resp.Body.Close()
				} else {
					pr, pw := io.Pipe()
					go func() {
						defer pw.Close()
						chunk := payload[:64*1024]
						rem := len(payload)
						for rem > 0 && time.Now().Before(deadline) {
							n := len(chunk)
							if rem < n {
								n = rem
							}
							written, werr := pw.Write(chunk[:n])
							if written > 0 {
								totalBytes.Add(int64(written))
								rem -= written
							}
							if werr != nil {
								return
							}
						}
					}()
					req, _ := http.NewRequestWithContext(ctx, "POST", url, pr)
					req.ContentLength = int64(len(payload))
					req.Header.Set("Content-Type", "application/octet-stream")
					req.Header.Set("User-Agent", "SplitRoute/1.0.0")
					resp, err := client.Do(req)
					if err != nil {
						setErr(err)
						time.Sleep(250 * time.Millisecond)
						continue
					}
					_, _ = io.Copy(io.Discard, resp.Body)
					resp.Body.Close()
				}
			}
		}()
	}

	var loadedPings []float64
	var lpMu sync.Mutex
	if pingURL != "" {
		wg.Add(1)
		go func() {
			defer wg.Done()
			time.Sleep(1 * time.Second)
			pClient := newHTTPClient(iface, "", 3*time.Second)
			for time.Now().Before(deadline) {
				t0 := time.Now()
				req, _ := http.NewRequest("GET", pingURL, nil)
				resp, err := pClient.Do(req)
				if err == nil {
					_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1024))
					resp.Body.Close()
					ms := float64(time.Since(t0).Microseconds()) / 1000.0
					lpMu.Lock()
					loadedPings = append(loadedPings, ms)
					lpMu.Unlock()
				}
				time.Sleep(400 * time.Millisecond)
			}
		}()
	}

	start := time.Now()
	lastTime := start
	var lastBytes int64
	var warmBytes int64
	var warmTime time.Time
	liveMbps := 0.0

	for time.Now().Before(deadline) {
		time.Sleep(200 * time.Millisecond)
		now := time.Now()
		b := totalBytes.Load()
		dt := now.Sub(lastTime).Seconds()
		if dt < 0.05 {
			dt = 0.05
		}
		instant := float64(b-lastBytes) * 8.0 / 1_000_000.0 / dt
		if liveMbps == 0 {
			liveMbps = instant
		} else {
			liveMbps = liveMbps*0.7 + instant*0.3
		}
		lastBytes = b
		lastTime = now
		if warmTime.IsZero() && now.Sub(start).Seconds() >= 1.0 {
			warmTime = now
			warmBytes = b
		}
		prog := now.Sub(start).Seconds() / seconds
		if prog > 1 {
			prog = 1
		}
		emit(speedEvent{Type: "sample", Mbps: liveMbps, Bytes: b, Progress: prog})
	}

	cancel()
	wg.Wait()

	end := time.Now()
	total := totalBytes.Load()
	from := start
	measuredBytes := total
	if !warmTime.IsZero() {
		from = warmTime
		measuredBytes = total - warmBytes
	}
	dur := end.Sub(from).Seconds()
	if dur < 0.5 {
		dur = 0.5
	}
	finalMbps := float64(measuredBytes) * 8.0 / 1_000_000.0 / dur
	if finalMbps < 0 {
		finalMbps = 0
	}

	var lp float64
	if len(loadedPings) > 0 {
		// median
		for i := 0; i < len(loadedPings); i++ {
			for j := i + 1; j < len(loadedPings); j++ {
				if loadedPings[j] < loadedPings[i] {
					loadedPings[i], loadedPings[j] = loadedPings[j], loadedPings[i]
				}
			}
		}
		lp = loadedPings[len(loadedPings)/2]
	}

	ev := speedEvent{Type: "done", Mbps: finalMbps, Bytes: total, LoadedMs: lp, Progress: 1.0}
	if total == 0 && firstErr != "" {
		ev.Type = "error"
		ev.Error = firstErr
	}
	emit(ev)
}

func main() {
	if len(os.Args) < 2 {
		os.Exit(64)
	}
	sub := os.Args[1]
	fs := flag.NewFlagSet(sub, flag.ExitOnError)
	iface := fs.String("interface", "", "interface name")
	v6 := fs.Bool("v6", false, "use IPv6")
	url := fs.String("url", "", "target URL")
	pingURL := fs.String("ping-url", "", "loaded ping URL")
	seconds := fs.Float64("seconds", 10, "duration seconds")
	streams := fs.Int("streams", 4, "parallel streams")
	_ = fs.Parse(os.Args[2:])

	switch sub {
	case "public-ip":
		cmdPublicIP(*iface, *v6)
	case "ping":
		cmdPing(*iface, *url)
	case "download", "upload":
		cmdTransfer(sub, *iface, *url, *pingURL, *seconds, *streams)
	default:
		os.Exit(64)
	}
}

