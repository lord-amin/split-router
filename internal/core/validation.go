package core

import (
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"strings"
)

// Validation helpers for IPv4, IPv6, CIDR, domain names, and Linux interface names.

func ParseIPv4(s string) (uint32, bool) {
	s = strings.TrimSpace(s)
	ip := net.ParseIP(s)
	if ip == nil {
		return 0, false
	}
	v4 := ip.To4()
	if v4 == nil {
		return 0, false
	}
	// Reject non-canonical forms like leading zeros if net.ParseIP accepted them? net.ParseIP rejects leading zeros in Go 1.17+.
	val := uint32(v4[0])<<24 | uint32(v4[1])<<16 | uint32(v4[2])<<8 | uint32(v4[3])
	return val, true
}

func IsValidIPv4(s string) bool {
	_, ok := ParseIPv4(s)
	return ok
}

func FormatIPv4(v uint32) string {
	return net.IPv4(byte(v>>24), byte(v>>16), byte(v>>8), byte(v)).String()
}

func MaskIPv4(prefix int) uint32 {
	if prefix <= 0 {
		return 0
	}
	if prefix >= 32 {
		return ^uint32(0)
	}
	return ^uint32(0) << uint(32-prefix)
}

func IPv4Contains(network string, prefix int, ip string) bool {
	n, ok1 := ParseIPv4(network)
	a, ok2 := ParseIPv4(ip)
	if !ok1 || !ok2 {
		return false
	}
	m := MaskIPv4(prefix)
	return (n & m) == (a & m)
}

func IsPrivateIPv4(s string) bool {
	v, ok := ParseIPv4(s)
	if !ok {
		return false
	}
	return IPv4Contains("10.0.0.0", 8, s) ||
		IPv4Contains("172.16.0.0", 12, s) ||
		IPv4Contains("192.168.0.0", 16, s) ||
		IPv4Contains("100.64.0.0", 10, s) ||
		(v>>24) == 127 ||
		IPv4Contains("169.254.0.0", 16, s)
}

func IsValidIPv6(s string) bool {
	s = strings.TrimSpace(s)
	if idx := strings.IndexByte(s, '%'); idx >= 0 {
		s = s[:idx]
	}
	ip := net.ParseIP(s)
	return ip != nil && ip.To4() == nil
}

func IsValidDomain(raw string) bool {
	d := strings.ToLower(strings.TrimSpace(raw))
	d = strings.TrimSuffix(d, ".")
	if len(d) == 0 || len(d) > 253 {
		return false
	}
	labels := strings.Split(d, ".")
	if len(labels) < 2 {
		return false
	}
	for _, label := range labels {
		if len(label) < 1 || len(label) > 63 {
			return false
		}
		if label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for i := 0; i < len(label); i++ {
			c := label[i]
			if !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-') {
				return false
			}
		}
	}
	tld := labels[len(labels)-1]
	allDigits := true
	for i := 0; i < len(tld); i++ {
		if tld[i] < '0' || tld[i] > '9' {
			allDigits = false
			break
		}
	}
	return !allDigits
}

// IsValidInterfaceName validates Linux network interface names (IFNAMSIZ-1 = 15 chars).
// Allows letters, digits, hyphens, underscores, and dots (e.g. enp3s0, br-1234, tun0, wg0, eth0.10).
func IsValidInterfaceName(s string) bool {
	if len(s) < 2 || len(s) > 15 {
		return false
	}
	first := s[0]
	if !((first >= 'a' && first <= 'z') || (first >= 'A' && first <= 'Z')) {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		ok := (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.'
		if !ok {
			return false
		}
	}
	return true
}

func NormalizeCIDR(s string) (string, bool) {
	s = strings.TrimSpace(strings.ToLower(s))
	if s == "" {
		return "", false
	}
	if strings.Contains(s, "/") {
		ip, ipNet, err := net.ParseCIDR(s)
		if err != nil {
			return "", false
		}
		if v4 := ip.To4(); v4 != nil {
			ones, _ := ipNet.Mask.Size()
			masked := ipNet.IP.To4()
			return masked.String() + "/" + itoa(ones), true
		}
		ones, _ := ipNet.Mask.Size()
		return ipNet.IP.String() + "/" + itoa(ones), true
	}
	if v, ok := ParseIPv4(s); ok {
		return FormatIPv4(v) + "/32", true
	}
	if IsValidIPv6(s) {
		ip := net.ParseIP(s)
		return ip.String() + "/128", true
	}
	return "", false
}

func CIDRContains(cidr string, ipStr string) bool {
	_, ipNet, err := net.ParseCIDR(cidr)
	if err != nil {
		return false
	}
	ip := net.ParseIP(strings.TrimSpace(ipStr))
	if ip == nil {
		return false
	}
	return ipNet.Contains(ip)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

// JSON helpers matching BIRJSON atomic writes.

func LoadJSON[T any](path string) (T, bool) {
	var zero T
	data, err := os.ReadFile(path)
	if err != nil {
		return zero, false
	}
	var v T
	if err := json.Unmarshal(data, &v); err != nil {
		return zero, false
	}
	return v, true
}

func SaveJSON(v any, path string, mode os.FileMode) error {
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, mode); err != nil {
		return err
	}
	if err := os.Chmod(tmp, mode); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, path)
}

