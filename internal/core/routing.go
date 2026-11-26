package core

import (
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
)

type RouteLookup struct {
	RouteTo   string `json:"routeTo"`
	Gateway   string `json:"gateway,omitempty"`
	Interface string `json:"interface,omitempty"`
	PrefSrc   string `json:"prefSrc,omitempty"`
	UID       int    `json:"uid,omitempty"`
}

type RouteEntry struct {
	ID          string   `json:"id"`
	Family      string   `json:"family"`
	Destination string   `json:"destination"`
	Gateway     string   `json:"gateway"`
	Protocol    string   `json:"protocol"`
	Scope       string   `json:"scope"`
	Netif       string   `json:"netif"`
	Metric      int      `json:"metric"`
	Flags       []string `json:"flags"`
}

func (e RouteEntry) IsDefault() bool {
	return e.Destination == "default" || e.Destination == "0.0.0.0/0" || e.Destination == "::/0"
}

func (e RouteEntry) IsStatic() bool {
	return e.Protocol == "static" || e.Protocol == "boot" || e.Protocol == ""
}

func (e RouteEntry) CIDR() (string, int, bool) {
	if e.Family != "inet" {
		return "", 0, false
	}
	if e.IsDefault() {
		return "0.0.0.0", 0, true
	}
	if strings.Contains(e.Destination, "/") {
		parts := strings.SplitN(e.Destination, "/", 2)
		p, err := strconv.Atoi(parts[1])
		if err != nil || !IsValidIPv4(parts[0]) {
			return "", 0, false
		}
		return parts[0], p, true
	}
	if IsValidIPv4(e.Destination) {
		return e.Destination, 32, true
	}
	return "", 0, false
}

type ipRouteJSON struct {
	Dst      string   `json:"dst"`
	Gateway  string   `json:"gateway"`
	Dev      string   `json:"dev"`
	Protocol string   `json:"protocol"`
	Scope    string   `json:"scope"`
	PrefSrc  string   `json:"prefsrc"`
	Metric   int      `json:"metric"`
	Flags    []string `json:"flags"`
}

type ipRouteGetJSON struct {
	Dst     string `json:"dst"`
	Gateway string `json:"gateway"`
	Dev     string `json:"dev"`
	PrefSrc string `json:"prefsrc"`
	UID     int    `json:"uid"`
}

func LookupRoute(destination string) *RouteLookup {
	res := RunCmd(4*time.Second, "ip", "-j", "route", "get", destination)
	if !res.OK() {
		return nil
	}
	var items []ipRouteGetJSON
	if err := json.Unmarshal([]byte(res.Stdout), &items); err != nil || len(items) == 0 {
		return nil
	}
	it := items[0]
	return &RouteLookup{
		RouteTo:   it.Dst,
		Gateway:   it.Gateway,
		Interface: it.Dev,
		PrefSrc:   it.PrefSrc,
		UID:       it.UID,
	}
}

func RouteTable(family string) []RouteEntry {
	args := []string{"-j", "-4", "route", "show"}
	if family == "inet6" {
		args = []string{"-j", "-6", "route", "show"}
	}
	res := RunCmd(8*time.Second, "ip", args...)
	if !res.OK() {
		return nil
	}
	var items []ipRouteJSON
	if err := json.Unmarshal([]byte(res.Stdout), &items); err != nil {
		return nil
	}
	out := make([]RouteEntry, 0, len(items))
	for idx, it := range items {
		out = append(out, RouteEntry{
			ID:          fmt.Sprintf("%s-%d", family, idx),
			Family:      family,
			Destination: it.Dst,
			Gateway:     it.Gateway,
			Protocol:    it.Protocol,
			Scope:       it.Scope,
			Netif:       it.Dev,
			Metric:      it.Metric,
			Flags:       it.Flags,
		})
	}
	return out
}

// RouterOf finds the DHCP / configured gateway of an interface.
func RouterOf(iface string) string {
	if !IsValidInterfaceName(iface) {
		return ""
	}
	res := RunCmd(4*time.Second, "ip", "-j", "-4", "route", "show", "default", "dev", iface)
	if res.OK() {
		var items []ipRouteJSON
		if err := json.Unmarshal([]byte(res.Stdout), &items); err == nil {
			for _, it := range items {
				if IsValidIPv4(it.Gateway) {
					return it.Gateway
				}
			}
		}
	}
	// Fallback: ask NetworkManager in case a VPN deleted the physical default route.
	nm := RunCmd(4*time.Second, "nmcli", "-g", "IP4.GATEWAY", "dev", "show", iface)
	if nm.OK() {
		gw := strings.TrimSpace(nm.Stdout)
		if IsValidIPv4(gw) {
			return gw
		}
	}
	return ""
}

// Exact matching in the live main routing table for our managed routes.
func FindInstalledRoute(r ManagedRoute, table []RouteEntry) bool {
	targetDst := r.DestinationLabel()
	for _, e := range table {
		if e.Destination != targetDst {
			continue
		}
		if r.Gateway != "" {
			if e.Gateway == r.Gateway && (r.Interface == "" || e.Netif == r.Interface) {
				return true
			}
		} else {
			if e.Gateway == "" && e.Netif == r.Interface {
				return true
			}
		}
	}
	return false
}

func RouteReplaceArgs(r ManagedRoute) []string {
	args := []string{"route", "replace", r.Key()}
	if r.Gateway != "" {
		args = append(args, "via", r.Gateway)
	}
	if r.Interface != "" {
		args = append(args, "dev", r.Interface)
	}
	args = append(args, "metric", "10")
	return args
}

func RouteDeleteArgs(r ManagedRoute) []string {
	args := []string{"route", "del", r.Key()}
	if r.Gateway != "" {
		args = append(args, "via", r.Gateway)
	}
	if r.Interface != "" {
		args = append(args, "dev", r.Interface)
	}
	return args
}

func DisplayRouteCmd(args []string) string {
	return "ip " + strings.Join(args, " ")
}

type PlanResult struct {
	Routes   []ManagedRoute
	Statuses map[string]RuleStatus
	Cache    map[string][]string
}

func PlanRoutes(
	rules []RouteRule,
	interfaces []InterfaceSnapshot,
	routerFn func(string) string,
	resolveFn func(string) []string,
	cache map[string][]string,
) PlanResult {
	var routes []ManagedRoute
	seen := map[string]string{}
	statuses := map[string]RuleStatus{}
	newCache := map[string][]string{}
	for k, v := range cache {
		newCache[k] = append([]string(nil), v...)
	}

	findIface := func(name string) (InterfaceSnapshot, bool) {
		for _, s := range interfaces {
			if s.Name == name {
				return s, true
			}
		}
		return InterfaceSnapshot{}, false
	}

	for _, rule := range rules {
		st := RuleStatus{
			RuleID:   rule.ID,
			State:    StateApplied,
			Resolved: map[string][]string{},
			Routes:   []string{},
			Messages: []string{},
		}
		if !rule.Enabled {
			st.State = StateDisabled
			statuses[rule.ID] = st
			continue
		}
		if errMsg := rule.Via.ValidationError(); errMsg != "" {
			st.State = StateFailed
			st.Messages = append(st.Messages, errMsg)
			statuses[rule.ID] = st
			continue
		}

		var gateway, iface string
		switch rule.Via.Kind {
		case ViaGateway:
			gateway = rule.Via.Gateway
		case ViaInterfaceDirect:
			s, ok := findIface(rule.Via.Interface)
			if !ok || !s.IsUp {
				st.State = StateWaiting
				st.Messages = append(st.Messages, fmt.Sprintf("Interface %s is not present or is down", rule.Via.Interface))
				statuses[rule.ID] = st
				continue
			}
			iface = rule.Via.Interface
		case ViaInterfaceRouter:
			s, ok := findIface(rule.Via.Interface)
			if !ok || !s.IsUp || len(s.IPv4) == 0 {
				st.State = StateWaiting
				st.Messages = append(st.Messages, fmt.Sprintf("Interface %s is down or has no IPv4 address", rule.Via.Interface))
				statuses[rule.ID] = st
				continue
			}
			iface = rule.Via.Interface
			if r := routerFn(iface); r != "" {
				gateway = r
			} else if !s.IsPointToPoint {
				st.State = StateWaiting
				st.Messages = append(st.Messages, fmt.Sprintf("No router known for %s yet (DHCP not finished?)", iface))
				statuses[rule.ID] = st
				continue
			}
		}

		for _, rawDest := range rule.Destinations {
			dest, ok := ParseDestination(rawDest)
			if !ok {
				continue
			}
			type target struct {
				addr   string
				prefix int
				domain string
			}
			var targets []target
			switch dest.Kind {
			case DestHost:
				targets = []target{{dest.Address, 32, ""}}
			case DestNetwork:
				targets = []target{{dest.Address, dest.Prefix, ""}}
			case DestDomain:
				ips := resolveFn(dest.Domain)
				if len(ips) > 0 {
					sorted := uniqueSorted(ips)
					newCache[dest.Domain] = sorted
					st.Resolved[dest.Domain] = sorted
				} else if cached := cache[dest.Domain]; len(cached) > 0 {
					st.Resolved[dest.Domain] = cached
					st.Messages = append(st.Messages, fmt.Sprintf("DNS lookup for %s failed; using last known IPs", dest.Domain))
				} else {
					st.Messages = append(st.Messages, fmt.Sprintf("Could not resolve %s", dest.Domain))
				}
				for _, ip := range st.Resolved[dest.Domain] {
					targets = append(targets, target{ip, 32, dest.Domain})
				}
			}

			for _, t := range targets {
				key := fmt.Sprintf("%s/%d", t.addr, t.prefix)
				if gateway != "" && t.addr == gateway {
					st.Messages = append(st.Messages, fmt.Sprintf("Skipped %s: it is the gateway itself", t.addr))
					continue
				}
				if owner, exists := seen[key]; exists {
					if owner != rule.ID {
						st.Messages = append(st.Messages, fmt.Sprintf("%s is already routed by another rule; skipped", key))
					}
					continue
				}
				seen[key] = rule.ID
				routes = append(routes, ManagedRoute{
					RuleID:       rule.ID,
					Destination:  t.addr,
					Prefix:       t.prefix,
					Gateway:      gateway,
					Interface:    iface,
					SourceDomain: t.domain,
					KeepOnRemove: rule.KeepRoutesOnRemove,
				})
				st.Expected++
			}
		}
		statuses[rule.ID] = st
	}

	return PlanResult{Routes: routes, Statuses: statuses, Cache: newCache}
}

func uniqueSorted(items []string) []string {
	m := map[string]bool{}
	var out []string
	for _, it := range items {
		if !m[it] {
			m[it] = true
			out = append(out, it)
		}
	}
	sort.Strings(out)
	return out
}

