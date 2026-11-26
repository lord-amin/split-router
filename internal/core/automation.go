package core

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
)

type NetworkFingerprint struct {
	Interface string `json:"interface"`
	RouterIP  string `json:"routerIP"`
	RouterMAC string `json:"routerMAC,omitempty"`
}

func (n NetworkFingerprint) Label() string {
	mac := ""
	if n.RouterMAC != "" {
		mac = " (" + n.RouterMAC + ")"
	}
	return fmt.Sprintf("router %s%s on %s", n.RouterIP, mac, n.Interface)
}

func CurrentNetworkFingerprint(interfaces []InterfaceSnapshot) *NetworkFingerprint {
	for _, i := range interfaces {
		if !i.IsUp || i.IsPointToPoint || i.IsLoopback || len(i.IPv4) == 0 || !IsPhysicalInterfaceName(i.Name) {
			continue
		}
		router := RouterOf(i.Name)
		if router == "" {
			continue
		}
		return &NetworkFingerprint{
			Interface: i.Name,
			RouterIP:  router,
			RouterMAC: NeighbourMAC(router, i.Name),
		}
	}
	return nil
}

type ipNeighEntry struct {
	Dst    string `json:"dst"`
	Dev    string `json:"dev"`
	LLAddr string `json:"lladdr"`
}

func NeighbourMAC(ip, iface string) string {
	if !IsValidIPv4(ip) {
		return ""
	}
	args := []string{"-j", "neigh", "show", ip}
	if IsValidInterfaceName(iface) {
		args = append(args, "dev", iface)
	}
	res := RunCmd(3*time.Second, "ip", args...)
	if !res.OK() {
		return ""
	}
	var items []ipNeighEntry
	if err := json.Unmarshal([]byte(res.Stdout), &items); err != nil {
		return ""
	}
	for _, it := range items {
		if it.LLAddr != "" {
			return strings.ToLower(it.LLAddr)
		}
	}
	return ""
}

type NetworkMatch struct {
	ID        string `json:"id"`
	Label     string `json:"label"`
	RouterIP  string `json:"routerIP"`
	RouterMAC string `json:"routerMAC,omitempty"`
}

func (m NetworkMatch) Matches(fp *NetworkFingerprint) bool {
	if fp == nil {
		return false
	}
	if m.RouterMAC != "" && fp.RouterMAC != "" {
		return strings.EqualFold(m.RouterMAC, fp.RouterMAC)
	}
	return m.RouterIP == fp.RouterIP
}

type RoutingProfile struct {
	ID         string         `json:"id"`
	Name       string         `json:"name"`
	RuleIDs    []string       `json:"ruleIDs"`
	KillSwitch *bool          `json:"killSwitch,omitempty"`
	Networks   []NetworkMatch `json:"networks"`
}

type TriggerEvent struct {
	Kind      string `json:"kind"` // tunnelUp, tunnelDown, interfaceUp, interfaceDown, egressChanged, networkChanged
	Interface string `json:"interface,omitempty"`
}

func (e TriggerEvent) Label() string {
	switch e.Kind {
	case "tunnelUp":
		return "Full-tunnel VPN connects"
	case "tunnelDown":
		return "Full-tunnel VPN disconnects"
	case "interfaceUp":
		return e.Interface + " comes up"
	case "interfaceDown":
		return e.Interface + " goes down"
	case "egressChanged":
		return "Internet traffic switches interface"
	case "networkChanged":
		return "Joined a different network"
	default:
		return e.Kind
	}
}

func (e TriggerEvent) Equal(other TriggerEvent) bool {
	return e.Kind == other.Kind && e.Interface == other.Interface
}

type TriggerAction struct {
	Kind      string `json:"kind"` // activateProfile, clearProfile, killSwitchOn, killSwitchOff, killSwitchAuto, learnVPNServers
	ProfileID string `json:"profileID,omitempty"`
}

type TriggerRule struct {
	ID      string          `json:"id"`
	Name    string          `json:"name"`
	Enabled bool            `json:"enabled"`
	Event   TriggerEvent    `json:"event"`
	Actions []TriggerAction `json:"actions"`
	Notify  bool            `json:"notify"`
}

type DomainResolver struct {
	ID          string   `json:"id"`
	Domain      string   `json:"domain"`
	Nameservers []string `json:"nameservers"`
	Enabled     bool     `json:"enabled"`
}

func (d DomainResolver) ValidationError() string {
	dom := strings.ToLower(strings.TrimSpace(d.Domain))
	if !IsValidDomain(dom) {
		// Allow single-label local domains like "internal" or "corp" just like macOS.
		single := len(dom) > 0 && !strings.Contains(dom, ".")
		if single {
			for i := 0; i < len(dom); i++ {
				c := dom[i]
				if !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-') {
					single = false
					break
				}
			}
		}
		if !single {
			return fmt.Sprintf("“%s” is not a valid domain", d.Domain)
		}
	}
	if len(d.Nameservers) == 0 {
		return fmt.Sprintf("%s: add at least one name server", d.Domain)
	}
	for _, ns := range d.Nameservers {
		if !IsValidIPv4(ns) && !IsValidIPv6(ns) {
			return fmt.Sprintf("%s: “%s” is not an IP address", d.Domain, ns)
		}
	}
	return ""
}

type BlockEntry struct {
	ID      string `json:"id"`
	Value   string `json:"value"`
	Note    string `json:"note"`
	Enabled bool   `json:"enabled"`
}

type AutomationConfig struct {
	Profiles            []RoutingProfile `json:"profiles"`
	ManualProfileID     string           `json:"manualProfileID,omitempty"`
	AutoSwitchByNetwork bool             `json:"autoSwitchByNetwork"`
	Triggers            []TriggerRule    `json:"triggers"`
	Resolvers           []DomainResolver `json:"resolvers"`
	Blocked             []BlockEntry     `json:"blocked"`
}

func (a AutomationConfig) IsEmpty() bool {
	return len(a.Profiles) == 0 && len(a.Triggers) == 0 && len(a.Resolvers) == 0 && len(a.Blocked) == 0 && a.ManualProfileID == ""
}

func (a AutomationConfig) EffectiveProfile(overrideID string, network *NetworkFingerprint) (*RoutingProfile, string) {
	if overrideID != "" {
		for i := range a.Profiles {
			if a.Profiles[i].ID == overrideID {
				return &a.Profiles[i], "set by a trigger"
			}
		}
	}
	if a.AutoSwitchByNetwork && network != nil {
		for i := range a.Profiles {
			for _, nm := range a.Profiles[i].Networks {
				if nm.Matches(network) {
					return &a.Profiles[i], "matched network " + network.Label()
				}
			}
		}
	}
	if a.ManualProfileID != "" {
		for i := range a.Profiles {
			if a.Profiles[i].ID == a.ManualProfileID {
				return &a.Profiles[i], "chosen manually"
			}
		}
	}
	return nil, "no profile: all enabled rules apply"
}

type RuntimeState struct {
	ProfileOverride    string              `json:"profileOverride,omitempty"`
	ProfileCleared     bool                `json:"profileCleared,omitempty"`
	KillSwitchOverride *bool               `json:"killSwitchOverride,omitempty"`
	Network            *NetworkFingerprint `json:"network,omitempty"`
	InterfacesUp       map[string]bool     `json:"interfacesUp"`
	TunnelUp           bool                `json:"tunnelUp"`
	Egress             string              `json:"egress,omitempty"`
	ActiveProfileID    string              `json:"activeProfileID,omitempty"`
	KillSwitchArmed    *bool               `json:"killSwitchArmed,omitempty"`
}

type BIREvent struct {
	ID     string    `json:"id"`
	Date   time.Time `json:"date"`
	Kind   string    `json:"kind"`
	Title  string    `json:"title"`
	Detail string    `json:"detail"`
	Notify bool      `json:"notify"`
}

type ActiveProfileInfo struct {
	ID     string `json:"id,omitempty"`
	Name   string `json:"name,omitempty"`
	Reason string `json:"reason"`
}

type ResolverStatus struct {
	Active []string `json:"active"`
	Errors []string `json:"errors"`
}

func WatchedInterfaceStates(interfaces []InterfaceSnapshot) map[string]bool {
	out := map[string]bool{}
	for _, i := range interfaces {
		if i.IsLoopback {
			continue
		}
		if IsPhysicalInterfaceName(i.Name) || IsTunnelInterfaceName(i.Name) {
			up := i.IsUp && len(i.IPv4) > 0
			if IsTunnelInterfaceName(i.Name) && !up {
				continue
			}
			out[i.Name] = up
		}
	}
	return out
}

func SortedKeys(m map[string]bool) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

