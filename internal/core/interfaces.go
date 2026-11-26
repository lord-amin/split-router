package core

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

type ShellResult struct {
	Code   int
	Stdout string
	Stderr string
}

func (r ShellResult) OK() bool { return r.Code == 0 }

func (r ShellResult) Combined() string {
	s := strings.TrimSpace(r.Stdout)
	e := strings.TrimSpace(r.Stderr)
	if s != "" && e != "" {
		return s + "\n" + e
	}
	if s != "" {
		return s
	}
	return e
}

func RunCmd(timeout time.Duration, name string, args ...string) ShellResult {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	var outBuf, errBuf bytes.Buffer
	cmd.Stdout = &outBuf
	cmd.Stderr = &errBuf
	err := cmd.Run()
	code := 0
	if err != nil {
		code = 1
		if exitErr, ok := err.(*exec.ExitError); ok {
			code = exitErr.ExitCode()
		}
	}
	return ShellResult{Code: code, Stdout: outBuf.String(), Stderr: errBuf.String()}
}

func RunCmdStdin(timeout time.Duration, stdin string, name string, args ...string) ShellResult {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Stdin = strings.NewReader(stdin)
	var outBuf, errBuf bytes.Buffer
	cmd.Stdout = &outBuf
	cmd.Stderr = &errBuf
	err := cmd.Run()
	code := 0
	if err != nil {
		code = 1
		if exitErr, ok := err.(*exec.ExitError); ok {
			code = exitErr.ExitCode()
		}
	}
	return ShellResult{Code: code, Stdout: outBuf.String(), Stderr: errBuf.String()}
}

type InterfaceAddress struct {
	Address string `json:"address"`
	Prefix  int    `json:"prefix"`
	Peer    string `json:"peer,omitempty"`
}

type InterfaceSnapshot struct {
	Name          string             `json:"name"`
	Index         int                `json:"index"`
	Flags         []string           `json:"flags"`
	IsUp          bool               `json:"isUp"`
	IsRunning     bool               `json:"isRunning"`
	IsPointToPoint bool              `json:"isPointToPoint"`
	IsLoopback    bool               `json:"isLoopback"`
	MTU           int                `json:"mtu"`
	MAC           string             `json:"mac,omitempty"`
	IPv4          []InterfaceAddress `json:"ipv4"`
	IPv6          []InterfaceAddress `json:"ipv6"`
	RxBytes       uint64             `json:"rxBytes"`
	TxBytes       uint64             `json:"txBytes"`
	RxPackets     uint64             `json:"rxPackets"`
	TxPackets     uint64             `json:"txPackets"`
	RxErrors      uint64             `json:"rxErrors"`
	TxErrors      uint64             `json:"txErrors"`
}

type ipAddrEntry struct {
	IfIndex   int      `json:"ifindex"`
	IfName    string   `json:"ifname"`
	Flags     []string `json:"flags"`
	MTU       int      `json:"mtu"`
	LinkType  string   `json:"link_type"`
	Address   string   `json:"address"`
	OperState string   `json:"operstate"`
	AddrInfo  []struct {
		Family    string `json:"family"`
		Local     string `json:"local"`
		Peer      string `json:"peer"`
		PrefixLen int    `json:"prefixlen"`
	} `json:"addr_info"`
}

func SnapshotInterfaces() []InterfaceSnapshot {
	res := RunCmd(5*time.Second, "ip", "-j", "addr", "show")
	if !res.OK() {
		return nil
	}
	var entries []ipAddrEntry
	if err := json.Unmarshal([]byte(res.Stdout), &entries); err != nil {
		return nil
	}
	stats := readProcNetDev()
	out := make([]InterfaceSnapshot, 0, len(entries))
	for _, e := range entries {
		snap := InterfaceSnapshot{
			Name:  e.IfName,
			Index: e.IfIndex,
			Flags: e.Flags,
			MTU:   e.MTU,
		}
		for _, f := range e.Flags {
			switch f {
			case "UP":
				snap.IsUp = true
			case "RUNNING", "LOWER_UP":
				snap.IsRunning = true
			case "POINTOPOINT":
				snap.IsPointToPoint = true
			case "LOOPBACK":
				snap.IsLoopback = true
			}
		}
		if e.LinkType == "ether" && e.Address != "" && e.Address != "00:00:00:00:00:00" {
			snap.MAC = strings.ToLower(e.Address)
		}
		for _, a := range e.AddrInfo {
			switch a.Family {
			case "inet":
				snap.IPv4 = append(snap.IPv4, InterfaceAddress{
					Address: a.Local,
					Prefix:  a.PrefixLen,
					Peer:    a.Peer,
				})
			case "inet6":
				snap.IPv6 = append(snap.IPv6, InterfaceAddress{
					Address: a.Local,
					Prefix:  a.PrefixLen,
				})
			}
		}
		if st, ok := stats[e.IfName]; ok {
			snap.RxBytes = st[0]
			snap.RxPackets = st[1]
			snap.RxErrors = st[2]
			snap.TxBytes = st[3]
			snap.TxPackets = st[4]
			snap.TxErrors = st[5]
		}
		out = append(out, snap)
	}
	return out
}

func readProcNetDev() map[string][6]uint64 {
	out := map[string][6]uint64{}
	data, err := os.ReadFile("/proc/net/dev")
	if err != nil {
		return out
	}
	for _, line := range strings.Split(string(data), "\n")[2:] {
		colon := strings.IndexByte(line, ':')
		if colon < 0 {
			continue
		}
		name := strings.TrimSpace(line[:colon])
		fields := strings.Fields(line[colon+1:])
		if len(fields) < 12 {
			continue
		}
		rxB, _ := strconv.ParseUint(fields[0], 10, 64)
		rxP, _ := strconv.ParseUint(fields[1], 10, 64)
		rxE, _ := strconv.ParseUint(fields[2], 10, 64)
		txB, _ := strconv.ParseUint(fields[8], 10, 64)
		txP, _ := strconv.ParseUint(fields[9], 10, 64)
		txE, _ := strconv.ParseUint(fields[10], 10, 64)
		out[name] = [6]uint64{rxB, rxP, rxE, txB, txP, txE}
	}
	return out
}

func IsPhysicalInterfaceName(name string) bool {
	if strings.HasPrefix(name, "en") || strings.HasPrefix(name, "eth") || strings.HasPrefix(name, "wl") || strings.HasPrefix(name, "usb") {
		return true
	}
	_, err := os.Stat("/sys/class/net/" + name + "/device")
	return err == nil
}

func IsTunnelInterfaceName(name string) bool {
	prefixes := []string{"tun", "tap", "wg", "utun", "ppp", "ipsec", "xfrm", "tailscale", "nordlynx", "mullvad", "proton", "nebula", "zt"}
	for _, p := range prefixes {
		if strings.HasPrefix(name, p) {
			return true
		}
	}
	return false
}

func ResolveIPv4(host string, timeout time.Duration) []string {
	return resolveHost(host, "ip4", timeout)
}

func ResolveIPv6(host string, timeout time.Duration) []string {
	return resolveHost(host, "ip6", timeout)
}

func resolveHost(host string, network string, timeout time.Duration) []string {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	r := &net.Resolver{}
	ips, err := r.LookupIP(ctx, network, host)
	if err != nil {
		return nil
	}
	seen := map[string]bool{}
	var out []string
	for _, ip := range ips {
		s := ip.String()
		if strings.HasPrefix(strings.ToLower(s), "::ffff:") {
			continue
		}
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	return out
}

