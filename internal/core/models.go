package core

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

const Version = "1.0.0"

// Shared system paths for SplitRoute on Linux.
const (
	HelperLabel  = "splitroute-helper"
	HelperBinary = "/usr/local/libexec/splitroute-helper"
	ProbeBinary  = "/usr/local/libexec/splitroute-probe"
	SystemdUnit  = "/etc/systemd/system/splitroute-helper.service"

	// Root-owned, survives reboots.
	SystemDir       = "/etc/splitroute"
	PersistentRules = SystemDir + "/rules.json"
	DNSCache        = SystemDir + "/dns-cache.json"
	KillSwitchFile  = SystemDir + "/killswitch.json"
	LearnedServers  = SystemDir + "/learned-vpn-servers.json"
	AutomationFile  = SystemDir + "/automation.json"

	// Cleared at every reboot (/run is tmpfs on Linux).
	RunDir       = "/run/splitroute"
	SessionRules = RunDir + "/session-rules.json"
	AppliedState = RunDir + "/applied.json"
	StatusFile   = RunDir + "/status.json"
	LockFile     = RunDir + "/lock"
	NFTFile      = RunDir + "/killswitch.nft"
	RuntimeFile  = RunDir + "/runtime.json"
	EventsFile   = RunDir + "/events.json"

	NFTTable = "inet splitroute"
	LogFile  = "/var/log/splitroute.log"
)

type RouteViaKind string

const (
	ViaInterfaceRouter RouteViaKind = "interfaceRouter"
	ViaInterfaceDirect RouteViaKind = "interfaceDirect"
	ViaGateway         RouteViaKind = "gateway"
)

type RouteVia struct {
	Kind      RouteViaKind `json:"kind"`
	Interface string       `json:"interface,omitempty"`
	Gateway   string       `json:"gateway,omitempty"`
}

func (v RouteVia) ValidationError() string {
	switch v.Kind {
	case ViaInterfaceRouter, ViaInterfaceDirect:
		if !IsValidInterfaceName(v.Interface) {
			return "Choose a valid interface"
		}
		return ""
	case ViaGateway:
		if !IsValidIPv4(v.Gateway) {
			return "Gateway must be an IPv4 address"
		}
		return ""
	default:
		return "Invalid route mode"
	}
}

type RouteRule struct {
	ID                 string    `json:"id"`
	Name               string    `json:"name"`
	Destinations       []string  `json:"destinations"`
	Via                RouteVia  `json:"via"`
	Enabled            bool      `json:"enabled"`
	Persistent         bool      `json:"persistent"`
	Notes              string    `json:"notes"`
	UpdatedAt          time.Time `json:"updatedAt"`
	KeepRoutesOnRemove bool      `json:"keepRoutesOnRemove,omitempty"`
}

type DestinationKind string

const (
	DestHost    DestinationKind = "host"
	DestNetwork DestinationKind = "network"
	DestDomain  DestinationKind = "domain"
)

type Destination struct {
	Kind    DestinationKind
	Address string
	Prefix  int
	Domain  string
}

func ParseDestination(raw string) (Destination, bool) {
	s := strings.ToLower(strings.TrimSpace(raw))
	if s == "" {
		return Destination{}, false
	}
	if slash := strings.IndexByte(s, '/'); slash >= 0 {
		addr := s[:slash]
		pStr := s[slash+1:]
		val, ok := ParseIPv4(addr)
		p, err := strconv.Atoi(pStr)
		if !ok || err != nil || p < 1 || p > 32 {
			return Destination{}, false
		}
		if p == 32 {
			return Destination{Kind: DestHost, Address: FormatIPv4(val), Prefix: 32}, true
		}
		netAddr := FormatIPv4(val & MaskIPv4(p))
		return Destination{Kind: DestNetwork, Address: netAddr, Prefix: p}, true
	}
	if val, ok := ParseIPv4(s); ok {
		return Destination{Kind: DestHost, Address: FormatIPv4(val), Prefix: 32}, true
	}
	if IsValidDomain(s) {
		return Destination{Kind: DestDomain, Domain: strings.TrimSuffix(s, ".")}, true
	}
	return Destination{}, false
}

func (r RouteRule) ValidationErrors() []string {
	var errs []string
	if strings.TrimSpace(r.Name) == "" {
		errs = append(errs, "Name is required")
	}
	if len(r.Destinations) == 0 {
		errs = append(errs, "Add at least one IP, network or domain")
	}
	for _, d := range r.Destinations {
		if _, ok := ParseDestination(d); !ok {
			errs = append(errs, fmt.Sprintf("“%s” is not a valid IPv4 address, CIDR network or domain", d))
		}
	}
	if e := r.Via.ValidationError(); e != "" {
		errs = append(errs, e)
	}
	return errs
}

type HelperConfig struct {
	Version int         `json:"version"`
	Rules   []RouteRule `json:"rules"`
}

type ManagedRoute struct {
	RuleID       string `json:"ruleID"`
	Destination  string `json:"destination"`
	Prefix       int    `json:"prefix"`
	Gateway      string `json:"gateway,omitempty"`
	Interface    string `json:"interface,omitempty"`
	SourceDomain string `json:"sourceDomain,omitempty"`
	KeepOnRemove bool   `json:"keepOnRemove,omitempty"`
}

func (m ManagedRoute) Key() string {
	return fmt.Sprintf("%s/%d", m.Destination, m.Prefix)
}

func (m ManagedRoute) IsHost() bool {
	return m.Prefix == 32
}

func (m ManagedRoute) DestinationLabel() string {
	if m.IsHost() {
		return m.Destination
	}
	return m.Key()
}

func (m ManagedRoute) NextHopLabel() string {
	switch {
	case m.Gateway != "" && m.Interface != "":
		return fmt.Sprintf("%s (%s)", m.Gateway, m.Interface)
	case m.Gateway != "":
		return m.Gateway
	case m.Interface != "":
		return "direct on " + m.Interface
	default:
		return "?"
	}
}

type RuleState string

const (
	StateApplied  RuleState = "applied"
	StatePartial  RuleState = "partial"
	StateFailed   RuleState = "failed"
	StateWaiting  RuleState = "waiting"
	StateDisabled RuleState = "disabled"
)

type RuleStatus struct {
	RuleID   string              `json:"ruleID"`
	State    RuleState           `json:"state"`
	Resolved map[string][]string `json:"resolved"`
	Routes   []string            `json:"routes"`
	Expected int                 `json:"expected"`
	Messages []string            `json:"messages"`
}

func (s *RuleStatus) Finalize() {
	if s.State == StateDisabled || s.State == StateWaiting {
		return
	}
	if s.Expected == 0 {
		s.State = StateFailed
	} else if len(s.Routes) == s.Expected {
		s.State = StateApplied
	} else if len(s.Routes) == 0 {
		s.State = StateFailed
	} else {
		s.State = StatePartial
	}
}

type HelperStatus struct {
	UpdatedAt       time.Time        `json:"updatedAt"`
	Trigger         string           `json:"trigger"`
	HelperVersion   string           `json:"helperVersion"`
	Rules           []RuleStatus     `json:"rules"`
	InstalledRoutes []ManagedRoute   `json:"installedRoutes"`
	KillSwitch      *KillSwitchStatus `json:"killSwitch,omitempty"`
	Profile         *ActiveProfileInfo `json:"profile,omitempty"`
	Resolvers       *ResolverStatus  `json:"resolvers,omitempty"`
	BlockList       *BlockListStatus `json:"blockList,omitempty"`
	TunnelUp        *bool            `json:"tunnelUp,omitempty"`
}

