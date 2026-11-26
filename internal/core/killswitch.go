package core

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type KillSwitchMode string

const (
	KSModeDestinations KillSwitchMode = "destinations"
	KSModeFullSystem   KillSwitchMode = "fullSystem"
)

type BlockList struct {
	ID        string   `json:"id"`
	Name      string   `json:"name"`
	Entries   []string `json:"entries"`
	Enabled   bool     `json:"enabled"`
	SourceURL string   `json:"sourceURL,omitempty"`
}

const TelegramListID = "7E1E6A00-0000-4000-8000-000000000001"

type KillSwitchConfig struct {
	Enabled               bool           `json:"enabled"`
	Mode                  KillSwitchMode `json:"mode"`
	Interfaces            []string       `json:"interfaces"`
	BlockLists            []BlockList    `json:"blockLists"`
	Allow                 []string       `json:"allow"`
	AllowLocalNetworks    bool           `json:"allowLocalNetworks"`
	AllowRuleDestinations bool           `json:"allowRuleDestinations"`
	AllowDNS              bool           `json:"allowDNS"`
	AllowDHCP             bool           `json:"allowDHCP"`
	LearnVPNServers       bool           `json:"learnVPNServers"`
}

func ParseKSEntry(raw string) (kind string, val string, ok bool) {
	s := strings.ToLower(strings.TrimSpace(raw))
	if s == "" {
		return "", "", false
	}
	if c, isCIDR := NormalizeCIDR(s); isCIDR {
		return "cidr", c, true
	}
	if IsValidDomain(s) {
		return "domain", strings.TrimSuffix(s, "."), true
	}
	return "", "", false
}

type BlockListStatus struct {
	Active bool     `json:"active"`
	Denied []string `json:"denied"`
	Errors []string `json:"errors"`
}

type KillSwitchStatus struct {
	Active         bool           `json:"active"`
	Mode           KillSwitchMode `json:"mode"`
	Interfaces     []string       `json:"interfaces"`
	Blocked        []string       `json:"blocked"`
	Allowed        []string       `json:"allowed"`
	DNSServers     []string       `json:"dnsServers"`
	LearnedServers []string       `json:"learnedServers"`
	FirewallActive bool           `json:"pfEnabled"`
	Errors         []string       `json:"errors"`
}

var LinkLocalRanges = []string{
	"169.254.0.0/16",
	"224.0.0.0/4",
	"255.255.255.255/32",
	"fe80::/10",
	"ff00::/8",
}

func splitV4V6(items []string) (v4 []string, v6 []string) {
	m4 := map[string]bool{}
	m6 := map[string]bool{}
	for _, raw := range items {
		c, ok := NormalizeCIDR(raw)
		if !ok {
			continue
		}
		if strings.Contains(c, ":") {
			if !m6[c] {
				m6[c] = true
				v6 = append(v6, c)
			}
		} else {
			if !m4[c] {
				m4[c] = true
				v4 = append(v4, c)
			}
		}
	}
	sort.Strings(v4)
	sort.Strings(v6)
	return v4, v6
}

// CompileNFTables generates an atomic nftables ruleset in `table inet splitroute`.
// It never modifies any other nftables/iptables/ufw tables.
func CompileNFTables(
	mode KillSwitchMode,
	interfaces []string,
	blocked []string,
	allowed []string,
	dnsServers []string,
	allowDHCP bool,
	killSwitch bool,
	denied []string,
) string {
	var b strings.Builder
	fmt.Fprintf(&b, "#!/usr/sbin/nft -f\n")
	fmt.Fprintf(&b, "# SplitRoute firewall — generated %s. Do not edit.\n", time.Now().UTC().Format(time.RFC3339))
	fmt.Fprintf(&b, "add table %s\n", NFTTable)
	fmt.Fprintf(&b, "delete table %s\n\n", NFTTable)
	fmt.Fprintf(&b, "table %s {\n", NFTTable)

	writeSet := func(name, typ string, elems []string) {
		if len(elems) == 0 {
			return
		}
		fmt.Fprintf(&b, "  set %s {\n    type %s\n    flags interval\n    auto-merge\n    elements = { %s }\n  }\n", name, typ, strings.Join(elems, ", "))
	}

	denyV4, denyV6 := splitV4V6(denied)
	writeSet("sr_deny_v4", "ipv4_addr", denyV4)
	writeSet("sr_deny_v6", "ipv6_addr", denyV6)

	var ifs []string
	for _, i := range interfaces {
		if IsValidInterfaceName(i) {
			ifs = append(ifs, fmt.Sprintf("%q", i))
		}
	}
	ksActive := killSwitch && len(ifs) > 0
	var blockV4, blockV6, allowV4, allowV6, dnsV4, dnsV6 []string
	if ksActive {
		if mode == KSModeDestinations {
			blockV4, blockV6 = splitV4V6(blocked)
			writeSet("sr_block_v4", "ipv4_addr", blockV4)
			writeSet("sr_block_v6", "ipv6_addr", blockV6)
		} else {
			allowV4, allowV6 = splitV4V6(allowed)
			dnsV4, dnsV6 = splitV4V6(dnsServers)
			writeSet("sr_allow_v4", "ipv4_addr", allowV4)
			writeSet("sr_allow_v6", "ipv6_addr", allowV6)
			writeSet("sr_dns_v4", "ipv4_addr", dnsV4)
			writeSet("sr_dns_v6", "ipv6_addr", dnsV6)
		}
	}

	ifExpr := ""
	if len(ifs) == 1 {
		ifExpr = "oifname " + ifs[0]
	} else if len(ifs) > 1 {
		ifExpr = "oifname { " + strings.Join(ifs, ", ") + " }"
	}

	b.WriteString("  chain output {\n")
	b.WriteString("    type filter hook output priority filter; policy accept;\n")
	if len(denyV4) > 0 {
		b.WriteString("    ip daddr @sr_deny_v4 reject\n")
	}
	if len(denyV6) > 0 {
		b.WriteString("    ip6 daddr @sr_deny_v6 reject\n")
	}
	if ksActive {
		if mode == KSModeDestinations {
			if len(blockV4) > 0 {
				fmt.Fprintf(&b, "    %s ip daddr @sr_block_v4 drop\n", ifExpr)
			}
			if len(blockV6) > 0 {
				fmt.Fprintf(&b, "    %s ip6 daddr @sr_block_v6 drop\n", ifExpr)
			}
		} else {
			if len(allowV4) > 0 {
				fmt.Fprintf(&b, "    %s ip daddr @sr_allow_v4 accept\n", ifExpr)
			}
			if len(allowV6) > 0 {
				fmt.Fprintf(&b, "    %s ip6 daddr @sr_allow_v6 accept\n", ifExpr)
			}
			if len(dnsV4) > 0 {
				fmt.Fprintf(&b, "    %s ip daddr @sr_dns_v4 udp dport 53 accept\n", ifExpr)
				fmt.Fprintf(&b, "    %s ip daddr @sr_dns_v4 tcp dport 53 accept\n", ifExpr)
			}
			if len(dnsV6) > 0 {
				fmt.Fprintf(&b, "    %s ip6 daddr @sr_dns_v6 udp dport 53 accept\n", ifExpr)
				fmt.Fprintf(&b, "    %s ip6 daddr @sr_dns_v6 tcp dport 53 accept\n", ifExpr)
			}
			if allowDHCP {
				fmt.Fprintf(&b, "    %s udp sport 68 udp dport 67 accept\n", ifExpr)
				fmt.Fprintf(&b, "    %s udp sport 546 udp dport 547 accept\n", ifExpr)
			}
			fmt.Fprintf(&b, "    %s drop\n", ifExpr)
		}
	}
	b.WriteString("  }\n")

	b.WriteString("  chain input {\n")
	b.WriteString("    type filter hook input priority filter; policy accept;\n")
	if len(denyV4) > 0 {
		b.WriteString("    ip saddr @sr_deny_v4 drop\n")
	}
	if len(denyV6) > 0 {
		b.WriteString("    ip6 saddr @sr_deny_v6 drop\n")
	}
	b.WriteString("  }\n")
	b.WriteString("}\n")
	return b.String()
}

var VPNCoreNames = []string{
	"xray", "sing-box", "v2ray", "happd", "hiddify", "clash", "mihomo",
	"tun2socks", "trojan", "hysteria", "openvpn", "wireguard", "wg-quick",
	"xl2tpd", "pppd", "charon", "strongswan", "openconnect", "tailscaled", "nekoray", "nekobox",
}

// SystemDNSServers reads current upstream DNS resolvers via resolvectl or /etc/resolv.conf.
func SystemDNSServers() []string {
	seen := map[string]bool{}
	var out []string
	addIP := func(ip string) {
		ip = strings.TrimSpace(ip)
		if (IsValidIPv4(ip) || IsValidIPv6(ip)) && ip != "127.0.0.53" && !seen[ip] {
			seen[ip] = true
			out = append(out, ip)
		}
	}
	res := RunCmd(4*time.Second, "resolvectl", "dns")
	if res.OK() {
		for _, line := range strings.Split(res.Stdout, "\n") {
			colon := strings.IndexByte(line, ':')
			if colon < 0 {
				continue
			}
			for _, tok := range strings.Fields(line[colon+1:]) {
				if idx := strings.IndexByte(tok, '%'); idx >= 0 {
					tok = tok[:idx]
				}
				addIP(tok)
			}
		}
	}
	if len(out) == 0 {
		if data, err := os.ReadFile("/etc/resolv.conf"); err == nil {
			for _, line := range strings.Split(string(data), "\n") {
				f := strings.Fields(line)
				if len(f) >= 2 && f[0] == "nameserver" {
					addIP(f[1])
				}
			}
		}
	}
	return out
}

// VPNServerPeers inspects active sockets (via ss -tunap) + WireGuard peers to learn VPN server IPs.
func VPNServerPeers() map[string][]string {
	result := map[string]map[string]bool{}
	add := func(proc, remote string) {
		if remote == "" || strings.HasPrefix(remote, "127.") || remote == "::1" || remote == "*" {
			return
		}
		if !(IsValidIPv4(remote) || IsValidIPv6(remote)) {
			return
		}
		if result[proc] == nil {
			result[proc] = map[string]bool{}
		}
		result[proc][remote] = true
	}

	res := RunCmd(8*time.Second, "ss", "-tunap")
	if res.OK() {
		for _, line := range strings.Split(res.Stdout, "\n") {
			proc := extractSSProcess(line)
			if proc == "" {
				continue
			}
			lower := strings.ToLower(proc)
			matched := false
			for _, core := range VPNCoreNames {
				if strings.HasPrefix(lower, core) {
					matched = true
					break
				}
			}
			if !matched {
				continue
			}
			fields := strings.Fields(line)
			if len(fields) < 6 {
				continue
			}
			peer := fields[5]
			host := stripPort(peer)
			add(lower, host)
		}
	}

	// Also check WireGuard endpoints if wg is installed.
	if wg := RunCmd(3*time.Second, "wg", "show", "all", "endpoints"); wg.OK() {
		for _, line := range strings.Split(wg.Stdout, "\n") {
			f := strings.Fields(line)
			if len(f) >= 3 && f[2] != "(none)" {
				add("wireguard", stripPort(f[2]))
			}
		}
	}

	out := map[string][]string{}
	for k, set := range result {
		var list []string
		for ip := range set {
			list = append(list, ip)
		}
		sort.Strings(list)
		out[k] = list
	}
	return out
}

func extractSSProcess(line string) string {
	idx := strings.Index(line, `users:(("`)
	if idx < 0 {
		return ""
	}
	rest := line[idx+9:]
	end := strings.IndexByte(rest, '"')
	if end < 0 {
		return ""
	}
	return rest[:end]
}

func stripPort(addr string) string {
	if strings.HasPrefix(addr, "[") {
		if end := strings.IndexByte(addr, ']'); end > 0 {
			h := addr[1:end]
			if pct := strings.IndexByte(h, '%'); pct >= 0 {
				h = h[:pct]
			}
			return h
		}
	}
	if colon := strings.LastIndexByte(addr, ':'); colon > 0 {
		return addr[:colon]
	}
	return addr
}

func AttachedNetworks(interfaces []InterfaceSnapshot, names []string) []string {
	want := map[string]bool{}
	for _, n := range names {
		want[n] = true
	}
	var out []string
	for _, i := range interfaces {
		if !want[i.Name] {
			continue
		}
		for _, a := range i.IPv4 {
			if a.Peer != "" && IsValidIPv4(a.Peer) {
				out = append(out, a.Peer+"/32")
			}
			if v, ok := ParseIPv4(a.Address); ok && a.Prefix >= 0 && a.Prefix <= 32 {
				netAddr := FormatIPv4(v & MaskIPv4(a.Prefix))
				out = append(out, fmt.Sprintf("%s/%d", netAddr, a.Prefix))
			}
		}
	}
	return out
}

func ReadDefaultRoutePhysicalIfaces(interfaces []InterfaceSnapshot, routes []RouteEntry) []string {
	// Used to identify full-tunnel VPNs.
	counts := map[string]int{}
	hasDefault := map[string]int{}
	for _, r := range routes {
		if r.Destination == "0.0.0.0/1" || r.Destination == "128.0.0.0/1" {
			counts[r.Netif]++
		}
		if r.IsDefault() {
			if m, exists := hasDefault[r.Netif]; !exists || r.Metric < m {
				hasDefault[r.Netif] = r.Metric
			}
		}
	}
	ifaceByName := map[string]InterfaceSnapshot{}
	for _, s := range interfaces {
		ifaceByName[s.Name] = s
	}
	var full []string
	seen := map[string]bool{}
	for name, c := range counts {
		if c >= 2 {
			seen[name] = true
			full = append(full, name)
		}
	}
	// On Linux, PPP / WireGuard / OpenVPN also often install a default route on a tunnel interface with lower metric.
	if egress := LookupRoute("1.1.1.1"); egress != nil && egress.Interface != "" {
		if IsTunnelInterfaceName(egress.Interface) && !seen[egress.Interface] {
			seen[egress.Interface] = true
			full = append(full, egress.Interface)
		}
	}
	sort.Strings(full)
	_ = ifaceByName
	_ = filepath.Base
	return full
}

