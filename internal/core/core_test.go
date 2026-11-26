package core

import (
	"strings"
	"testing"
)

func TestValidationAndCIDR(t *testing.T) {
	if !IsValidIPv4("192.168.1.1") {
		t.Fatal("expected valid IPv4")
	}
	if IsValidIPv4("999.1.1.1") {
		t.Fatal("expected invalid IPv4")
	}
	if !IsPrivateIPv4("10.20.30.40") || !IsPrivateIPv4("192.168.1.5") || IsPrivateIPv4("1.1.1.1") {
		t.Fatal("unexpected private IPv4 classification")
	}
	if !IsValidDomain("api.telegram.org") || IsValidDomain("localhost") || IsValidDomain("1.2.3.4") {
		t.Fatal("unexpected domain validation")
	}
	if !IsValidInterfaceName("enp3s0") || !IsValidInterfaceName("br-ab904b") || IsValidInterfaceName("bad iface!") {
		t.Fatal("unexpected interface name validation")
	}
	c, ok := NormalizeCIDR("192.168.1.42/24")
	if !ok || c != "192.168.1.0/24" {
		t.Fatalf("expected 192.168.1.0/24, got %s", c)
	}
	if !CIDRContains("192.168.1.0/24", "192.168.1.99") || CIDRContains("192.168.1.0/24", "192.168.2.1") {
		t.Fatal("unexpected CIDRContains result")
	}
}

func TestRoutePlanner(t *testing.T) {
	ifaces := []InterfaceSnapshot{
		{
			Name: "enp3s0",
			IsUp: true,
			IPv4: []InterfaceAddress{{Address: "192.168.20.89", Prefix: 24}},
		},
	}
	rules := []RouteRule{
		{
			ID:           "r1",
			Name:         "Direct test",
			Destinations: []string{"1.1.1.1", "example.com", "10.0.0.0/8"},
			Via:          RouteVia{Kind: ViaInterfaceRouter, Interface: "enp3s0"},
			Enabled:      true,
			Persistent:   true,
		},
		{
			ID:           "r2",
			Name:         "Down iface",
			Destinations: []string{"8.8.8.8"},
			Via:          RouteVia{Kind: ViaInterfaceDirect, Interface: "tun0"},
			Enabled:      true,
		},
	}
	plan := PlanRoutes(
		rules,
		ifaces,
		func(i string) string {
			if i == "enp3s0" {
				return "192.168.20.1"
			}
			return ""
		},
		func(d string) []string {
			if d == "example.com" {
				return []string{"93.184.216.34"}
			}
			return nil
		},
		map[string][]string{},
	)
	if len(plan.Routes) != 3 {
		t.Fatalf("expected 3 routes, got %d", len(plan.Routes))
	}
	if plan.Statuses["r2"].State != StateWaiting {
		t.Fatalf("expected r2 waiting, got %s", plan.Statuses["r2"].State)
	}
}

func TestCompileNFTables(t *testing.T) {
	nft := CompileNFTables(
		KSModeFullSystem,
		[]string{"enp3s0", "wlp4s0"},
		nil,
		[]string{"192.168.20.0/24", "1.2.3.4"},
		[]string{"1.1.1.1"},
		true,
		true,
		[]string{"91.108.56.0/22", "2001:b28:f23d::/48"},
	)
	for _, want := range []string{
		"table inet splitroute",
		"set sr_deny_v4",
		"set sr_deny_v6",
		"set sr_allow_v4",
		`oifname { "enp3s0", "wlp4s0" } ip daddr @sr_allow_v4 accept`,
		`oifname { "enp3s0", "wlp4s0" } drop`,
		"ip daddr @sr_deny_v4 reject",
	} {
		if !strings.Contains(nft, want) {
			t.Fatalf("missing %q in nftables output:\n%s", want, nft)
		}
	}
}

