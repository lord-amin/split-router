package main

import (
	"bufio"
	"crypto/rand"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"splitroute/internal/core"
)

func main() {
	args := os.Args[1:]
	if len(args) == 0 {
		printUsage()
		os.Exit(64)
	}
	switch args[0] {
	case "version":
		fmt.Println(core.Version)
	case "plan":
		if len(args) < 2 {
			fail("usage: plan <rules.json>", 64)
		}
		cfg, ok := core.LoadJSON[core.HelperConfig](args[1])
		if !ok {
			fail("cannot read "+args[1], 1)
		}
		ifaces := core.SnapshotInterfaces()
		table := core.RouteTable("inet")
		plan := core.PlanRoutes(
			cfg.Rules,
			ifaces,
			core.RouterOf,
			func(h string) []string { return core.ResolveIPv4(h, 4*time.Second) },
			map[string][]string{},
		)
		for _, r := range plan.Routes {
			state := "missing"
			if core.FindInstalledRoute(r, table) {
				state = "installed"
			}
			keep := ""
			if r.KeepOnRemove {
				keep = " keep"
			}
			fmt.Printf("%s   [%s]%s\n", core.DisplayRouteCmd(core.RouteReplaceArgs(r)), state, keep)
		}
		for _, st := range plan.Statuses {
			for _, m := range st.Messages {
				fmt.Printf("note: %s\n", m)
			}
		}
	case "daemon":
		requireRoot()
		interval := 60
		for i := 1; i < len(args)-1; i++ {
			if args[i] == "--interval" {
				if v, err := strconv.Atoi(args[i+1]); err == nil && v >= 15 {
					interval = v
				}
			}
		}
		runDaemon(interval)
	case "apply":
		requireRoot()
		trigger := "manual"
		for i := 1; i < len(args)-1; i++ {
			if args[i] == "--trigger" {
				trigger = args[i+1]
			}
		}
		withLock(func() { apply(trigger) })
	case "remove-all":
		requireRoot()
		withLock(removeAll)
	case "killswitch-off":
		requireRoot()
		withLock(func() { killSwitchOff("manual") })
	case "detect-vpn":
		requireRoot()
		withLock(func() {
			peers := core.VPNServerPeers()
			keys := make([]string, 0, len(peers))
			for k := range peers {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				fmt.Printf("%s: %s\n", k, strings.Join(peers[k], ", "))
			}
			all := learnVPNServers()
			fmt.Printf("learned: %s\n", strings.Join(all, ", "))
		})
	default:
		printUsage()
		os.Exit(64)
	}
}

func printUsage() {
	fmt.Println("usage: splitroute-helper daemon [--interval s] | apply [--trigger name] | remove-all | killswitch-off | detect-vpn | plan <file> | version")
}

func requireRoot() {
	if os.Geteuid() != 0 {
		fail("must run as root", 77)
	}
}

func fail(msg string, code int) {
	fmt.Fprintf(os.Stderr, "error: %s\n", msg)
	os.Exit(code)
}

func logMsg(msg string) {
	line := fmt.Sprintf("%s %s\n", time.Now().UTC().Format(time.RFC3339), msg)
	fmt.Println(msg)
	f, err := os.OpenFile(core.LogFile, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err == nil {
		_, _ = f.WriteString(line)
		_ = f.Close()
	}
}

func ensureDirs() {
	_ = os.MkdirAll(core.RunDir, 0o755)
	_ = os.Chmod(core.RunDir, 0o755)
	_ = os.MkdirAll(core.SystemDir, 0o755)
}

func withLock(fn func()) {
	ensureDirs()
	f, err := os.OpenFile(core.LockFile, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		fail("cannot open lock file: "+err.Error(), 1)
	}
	defer f.Close()
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
		fail("cannot lock: "+err.Error(), 1)
	}
	defer func() { _ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN) }()
	fn()
}

func ipCmd(args ...string) core.ShellResult {
	return core.RunCmd(10*time.Second, "ip", args...)
}

func nftCmd(args ...string) core.ShellResult {
	return core.RunCmd(15*time.Second, "nft", args...)
}

func loadRules() []core.RouteRule {
	p, _ := core.LoadJSON[core.HelperConfig](core.PersistentRules)
	s, _ := core.LoadJSON[core.HelperConfig](core.SessionRules)
	seen := map[string]bool{}
	var out []core.RouteRule
	for _, r := range append(p.Rules, s.Rules...) {
		if !seen[r.ID] {
			seen[r.ID] = true
			out = append(out, r)
		}
	}
	return out
}

func removeRoute(old core.ManagedRoute, reason string, table []core.RouteEntry) bool {
	if old.KeepOnRemove {
		logMsg(fmt.Sprintf("left %s in place (%s; rule keeps its routes)", old.DestinationLabel(), reason))
		return false
	}
	if !core.FindInstalledRoute(old, table) {
		return false
	}
	r := ipCmd(core.RouteDeleteArgs(old)...)
	if r.OK() {
		logMsg(fmt.Sprintf("removed %s -> %s (%s)", old.DestinationLabel(), old.NextHopLabel(), reason))
	} else {
		logMsg(fmt.Sprintf("failed to remove %s: %s", old.DestinationLabel(), r.Combined()))
	}
	return r.OK()
}

func nftTableActive() bool {
	return nftCmd("list", "table", "inet", "splitroute").OK()
}

func killSwitchOff(reason string) {
	if nftTableActive() {
		r := nftCmd("delete", "table", "inet", "splitroute")
		if r.OK() {
			logMsg("kill switch / firewall off (" + reason + ")")
		}
	}
	_ = os.Remove(core.NFTFile)
}

func learnVPNServers() []string {
	existing, _ := core.LoadJSON[[]string](core.LearnedServers)
	set := map[string]bool{}
	for _, ip := range existing {
		set[ip] = true
	}
	before := len(set)
	for _, ips := range core.VPNServerPeers() {
		for _, ip := range ips {
			set[ip] = true
		}
	}
	list := core.SortedKeys(set)
	if len(list) != before {
		_ = core.SaveJSON(list, core.LearnedServers, 0o644)
		logMsg("learned VPN servers: " + strings.Join(list, ", "))
	}
	return list
}

func resolveEntries(entries []string, cache map[string][]string) ([]string, []string) {
	var cidrs []string
	var errors []string
	for _, raw := range entries {
		kind, val, ok := core.ParseKSEntry(raw)
		if !ok {
			errors = append(errors, "Ignored invalid entry "+raw)
			continue
		}
		if kind == "cidr" {
			cidrs = append(cidrs, val)
			continue
		}
		v4 := core.ResolveIPv4(val, 4*time.Second)
		v6 := core.ResolveIPv6(val, 3*time.Second)
		ips := append(v4, v6...)
		if len(ips) > 0 {
			cache["ks:"+val] = ips
		} else if len(cache["ks:"+val]) == 0 {
			errors = append(errors, "Could not resolve "+val)
		}
		for _, ip := range cache["ks:"+val] {
			if c, ok := core.NormalizeCIDR(ip); ok {
				cidrs = append(cidrs, c)
			}
		}
	}
	return cidrs, errors
}

func uniqueSortedStrings(in []string) []string {
	m := map[string]bool{}
	for _, s := range in {
		if s != "" {
			m[s] = true
		}
	}
	return core.SortedKeys(m)
}

func applyFirewall(
	routes []core.ManagedRoute,
	interfaces []core.InterfaceSnapshot,
	cache map[string][]string,
	enabledOverride *bool,
	blockEntries []core.BlockEntry,
) (*core.KillSwitchStatus, *core.BlockListStatus) {
	cfg, hasCfg := core.LoadJSON[core.KillSwitchConfig](core.KillSwitchFile)
	ksEnabled := false
	if hasCfg {
		ksEnabled = cfg.Enabled
	}
	if enabledOverride != nil {
		ksEnabled = *enabledOverride
	}

	var denyValues []string
	for _, b := range blockEntries {
		if b.Enabled {
			denyValues = append(denyValues, b.Value)
		}
	}
	var denied []string
	var denyErrors []string
	if len(denyValues) > 0 {
		var r []string
		r, denyErrors = resolveEntries(denyValues, cache)
		denied = uniqueSortedStrings(r)
	}

	if !ksEnabled && len(denied) == 0 {
		if _, err := os.Stat(core.NFTFile); err == nil || nftTableActive() {
			killSwitchOff("nothing to enforce")
		}
		var bl *core.BlockListStatus
		if len(denyValues) > 0 {
			bl = &core.BlockListStatus{Active: false, Denied: []string{}, Errors: denyErrors}
		}
		return nil, bl
	}

	var ifs []string
	for _, i := range cfg.Interfaces {
		if core.IsValidInterfaceName(i) {
			ifs = append(ifs, i)
		}
	}
	var ksErrors, blocked, allowed, dns, learned []string
	if ksEnabled {
		if cfg.LearnVPNServers {
			learned = learnVPNServers()
		}
		if cfg.Mode == core.KSModeDestinations {
			for _, l := range cfg.BlockLists {
				if !l.Enabled {
					continue
				}
				c, errs := resolveEntries(l.Entries, cache)
				blocked = append(blocked, c...)
				ksErrors = append(ksErrors, errs...)
			}
		} else {
			c, errs := resolveEntries(cfg.Allow, cache)
			allowed = append(allowed, c...)
			ksErrors = append(ksErrors, errs...)
			for _, ip := range learned {
				if norm, ok := core.NormalizeCIDR(ip); ok {
					allowed = append(allowed, norm)
				}
			}
			if cfg.AllowLocalNetworks {
				allowed = append(allowed, core.AttachedNetworks(interfaces, ifs)...)
				allowed = append(allowed, core.LinkLocalRanges...)
			}
			if cfg.AllowRuleDestinations {
				for _, r := range routes {
					allowed = append(allowed, r.Key())
				}
			}
			if cfg.AllowDNS {
				dns = core.SystemDNSServers()
			}
			if len(learned) == 0 && len(cfg.Allow) == 0 {
				ksErrors = append(ksErrors, "No VPN server is allowed yet: connect your VPN once and press “Detect VPN servers”, or the VPN cannot reconnect.")
			}
		}
		blocked = uniqueSortedStrings(blocked)
		allowed = uniqueSortedStrings(allowed)
	}

	text := core.CompileNFTables(cfg.Mode, ifs, blocked, allowed, dns, cfg.AllowDHCP, ksEnabled, denied)
	prevBytes, _ := os.ReadFile(core.NFTFile)
	prevBody := stripNFTHeader(string(prevBytes))
	nextBody := stripNFTHeader(text)
	changed := prevBody != nextBody || !nftTableActive()

	nftOK := true
	if changed {
		if err := os.WriteFile(core.NFTFile, []byte(text), 0o644); err != nil {
			ksErrors = append(ksErrors, "Cannot write nftables rules: "+err.Error())
			nftOK = false
		} else {
			res := nftCmd("-f", core.NFTFile)
			if !res.OK() {
				nftOK = false
				ksErrors = append(ksErrors, "nft rejected the rules: "+res.Combined())
				logMsg("firewall load failed: " + res.Combined())
			} else if prevBody != nextBody {
				var parts []string
				if len(denied) > 0 {
					parts = append(parts, fmt.Sprintf("block list %d ranges", len(denied)))
				}
				if ksEnabled {
					parts = append(parts, fmt.Sprintf("kill switch %s on %s", cfg.Mode, strings.Join(ifs, ", ")))
				}
				logMsg("firewall loaded: " + strings.Join(parts, "; "))
			}
		}
	}
	active := nftOK && nftTableActive()
	var ks *core.KillSwitchStatus
	if ksEnabled {
		ks = &core.KillSwitchStatus{
			Active:         active,
			Mode:           cfg.Mode,
			Interfaces:     ifs,
			Blocked:        blocked,
			Allowed:        allowed,
			DNSServers:     dns,
			LearnedServers: learned,
			FirewallActive: active,
			Errors:         ksErrors,
		}
	}
	var bl *core.BlockListStatus
	if len(denyValues) > 0 {
		errs := append([]string(nil), denyErrors...)
		if !nftOK {
			errs = append(errs, ksErrors...)
		}
		bl = &core.BlockListStatus{
			Active: active && len(denied) > 0,
			Denied: denied,
			Errors: errs,
		}
	}
	return ks, bl
}

func stripNFTHeader(s string) string {
	lines := strings.Split(s, "\n")
	var kept []string
	for _, l := range lines {
		if strings.HasPrefix(l, "# SplitRoute firewall") {
			continue
		}
		kept = append(kept, l)
	}
	return strings.Join(kept, "\n")
}

// Per-domain DNS and domain blocking via systemd-resolved dummy links + /etc/hosts fallback.

const hostsBegin = "# BEGIN SPLITROUTE BLOCKED DOMAINS"
const hostsEnd = "# END SPLITROUTE BLOCKED DOMAINS"

func applyResolvers(resolvers []core.DomainResolver, blockedDomains []string) core.ResolverStatus {
	errors := []string{}
	active := []string{}
	changed := false

	// 1. Clean up existing srdns* dummy links first if we need to rebuild.
	// Group enabled resolvers by sorted nameserver list.
	type group struct {
		ns      []string
		domains []string
		isBlock bool
	}
	groupsByKey := map[string]*group{}
	var groupOrder []string

	for _, r := range resolvers {
		if !r.Enabled {
			continue
		}
		if errMsg := r.ValidationError(); errMsg != "" {
			errors = append(errors, errMsg)
			continue
		}
		dom := strings.ToLower(strings.TrimSpace(r.Domain))
		ns := append([]string(nil), r.Nameservers...)
		sort.Strings(ns)
		key := "ns:" + strings.Join(ns, ",")
		if groupsByKey[key] == nil {
			groupsByKey[key] = &group{ns: r.Nameservers}
			groupOrder = append(groupOrder, key)
		}
		groupsByKey[key].domains = append(groupsByKey[key].domains, dom)
		active = append(active, dom)
	}

	resolvedActive := core.RunCmd(2*time.Second, "systemctl", "is-active", "--quiet", "systemd-resolved").OK()

	var cleanBlocked []string
	for _, d := range blockedDomains {
		dom := strings.ToLower(strings.TrimSpace(d))
		if !core.IsValidDomain(dom) {
			continue
		}
		hasCustom := false
		for _, a := range active {
			if a == dom {
				hasCustom = true
				break
			}
		}
		if hasCustom {
			errors = append(errors, dom+" is both blocked and has its own name servers; the name servers win")
			continue
		}
		cleanBlocked = append(cleanBlocked, dom)
	}
	sort.Strings(cleanBlocked)

	if resolvedActive {
		// Assign srdns0..srdnsN for custom resolvers, and srdnsblk for blocked domains (pointing to 127.0.0.59 sinkhole).
		desiredLinks := map[string]*group{}
		for idx, key := range groupOrder {
			ifName := fmt.Sprintf("srdns%d", idx)
			desiredLinks[ifName] = groupsByKey[key]
		}
		if len(cleanBlocked) > 0 {
			desiredLinks["srdnsblk"] = &group{
				ns:      []string{"127.0.0.59"},
				domains: cleanBlocked,
				isBlock: true,
			}
		}

		// Remove stale srdns* / legacy birdns* dummy links.
		existingIfaces := core.SnapshotInterfaces()
		for _, iface := range existingIfaces {
			if strings.HasPrefix(iface.Name, "srdns") || strings.HasPrefix(iface.Name, "birdns") {
				if _, keep := desiredLinks[iface.Name]; !keep {
					_ = ipCmd("link", "del", iface.Name)
					changed = true
				}
			}
		}

		// Ensure each desired link exists and has its resolvectl configuration.
		existingMap := map[string]core.InterfaceSnapshot{}
		for _, iface := range existingIfaces {
			existingMap[iface.Name] = iface
		}
		linkNames := make([]string, 0, len(desiredLinks))
		for ifName := range desiredLinks {
			linkNames = append(linkNames, ifName)
		}
		sort.Strings(linkNames)
		for linkIdx, ifName := range linkNames {
			g := desiredLinks[ifName]
			snap, exists := existingMap[ifName]
			if !exists {
				if r := ipCmd("link", "add", ifName, "type", "dummy"); !r.OK() {
					errors = append(errors, "Cannot create dummy DNS link "+ifName+": "+r.Combined())
					continue
				}
				changed = true
			}
			// systemd-resolved requires a non-link-local address on the link to activate DNS scope.
			if !exists || len(snap.IPv4) == 0 {
				octet := 10 + (linkIdx % 230)
				if ifName == "srdnsblk" {
					octet = 250
				}
				_ = ipCmd("addr", "replace", fmt.Sprintf("100.127.254.%d/32", octet), "dev", ifName)
			}
			_ = ipCmd("link", "set", ifName, "up")
			_ = core.RunCmd(3*time.Second, "resolvectl", "default-route", ifName, "no")
			dnsArgs := append([]string{"dns", ifName}, g.ns...)
			if r := core.RunCmd(3*time.Second, "resolvectl", dnsArgs...); !r.OK() {
				errors = append(errors, "resolvectl dns "+ifName+" failed: "+r.Combined())
			}
			domArgs := []string{"domain", ifName}
			for _, d := range g.domains {
				domArgs = append(domArgs, "~"+d)
			}
			if r := core.RunCmd(3*time.Second, "resolvectl", domArgs...); !r.OK() {
				errors = append(errors, "resolvectl domain "+ifName+" failed: "+r.Combined())
			}
		}
		if len(desiredLinks) > 0 || changed {
			_ = core.RunCmd(3*time.Second, "resolvectl", "flush-caches")
		}
	} else if len(groupOrder) > 0 {
		errors = append(errors, "systemd-resolved is not active; per-domain DNS requires systemd-resolved.")
	}

	// Also maintain /etc/hosts block section for exact blocked domains (works even with static /etc/resolv.conf).
	if updateHostsBlock(cleanBlocked) {
		changed = true
		if resolvedActive {
			_ = core.RunCmd(3*time.Second, "resolvectl", "flush-caches")
		}
	}
	_ = changed

	sort.Strings(active)
	return core.ResolverStatus{Active: active, Errors: errors}
}

func updateHostsBlock(domains []string) bool {
	raw, err := os.ReadFile("/etc/hosts")
	if err != nil {
		return false
	}
	text := string(raw)
	startIdx := strings.Index(text, hostsBegin)
	endIdx := strings.Index(text, hostsEnd)
	var base string
	if startIdx >= 0 && endIdx > startIdx {
		before := strings.TrimRight(text[:startIdx], "\n")
		after := strings.TrimLeft(text[endIdx+len(hostsEnd):], "\n")
		if after != "" {
			base = before + "\n" + after
		} else {
			base = before
		}
	} else {
		base = strings.TrimRight(text, "\n")
	}

	var block string
	if len(domains) > 0 {
		var lines []string
		lines = append(lines, hostsBegin)
		for _, d := range domains {
			lines = append(lines, "0.0.0.0 "+d)
			lines = append(lines, ":: "+d)
		}
		lines = append(lines, hostsEnd)
		block = "\n\n" + strings.Join(lines, "\n") + "\n"
	} else {
		block = "\n"
	}
	next := base + block
	if next == text {
		return false
	}
	_ = os.WriteFile("/etc/hosts", []byte(next), 0o644)
	return true
}

func newUUID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

func recordEvents(newEvents []core.BIREvent) {
	if len(newEvents) == 0 {
		return
	}
	all, _ := core.LoadJSON[[]core.BIREvent](core.EventsFile)
	all = append(all, newEvents...)
	if len(all) > 200 {
		all = all[len(all)-200:]
	}
	_ = core.SaveJSON(all, core.EventsFile, 0o644)
	for _, e := range newEvents {
		detail := ""
		if e.Detail != "" {
			detail = " — " + e.Detail
		}
		logMsg("event: " + e.Title + detail)
	}
}

func detectEvents(
	cfg core.AutomationConfig,
	runtime *core.RuntimeState,
	firstRun bool,
	interfaces []core.InterfaceSnapshot,
	table []core.RouteEntry,
) []core.BIREvent {
	tunnels := core.ReadDefaultRoutePhysicalIfaces(interfaces, table)
	tunnelUp := len(tunnels) > 0
	states := core.WatchedInterfaceStates(interfaces)
	egress := ""
	if l := core.LookupRoute("1.1.1.1"); l != nil {
		egress = l.Interface
	}
	network := core.CurrentNetworkFingerprint(interfaces)

	defer func() {
		runtime.TunnelUp = tunnelUp
		runtime.InterfacesUp = states
		runtime.Egress = egress
		runtime.Network = network
	}()

	if firstRun {
		return nil
	}

	type firedEv struct {
		ev     core.TriggerEvent
		detail string
	}
	var fired []firedEv
	if tunnelUp != runtime.TunnelUp {
		kind := "tunnelDown"
		det := ""
		if tunnelUp {
			kind = "tunnelUp"
			det = "on " + strings.Join(tunnels, ", ")
		}
		fired = append(fired, firedEv{core.TriggerEvent{Kind: kind}, det})
	}
	for name, up := range states {
		if runtime.InterfacesUp[name] != up {
			kind := "interfaceDown"
			if up {
				kind = "interfaceUp"
			}
			fired = append(fired, firedEv{core.TriggerEvent{Kind: kind, Interface: name}, ""})
		}
	}
	for name, wasUp := range runtime.InterfacesUp {
		if _, exists := states[name]; wasUp && !exists {
			fired = append(fired, firedEv{core.TriggerEvent{Kind: "interfaceDown", Interface: name}, ""})
		}
	}
	if egress != runtime.Egress {
		prev := runtime.Egress
		if prev == "" {
			prev = "none"
		}
		curr := egress
		if curr == "" {
			curr = "none"
		}
		fired = append(fired, firedEv{core.TriggerEvent{Kind: "egressChanged"}, prev + " → " + curr})
	}
	prevIP, prevMAC := "", ""
	if runtime.Network != nil {
		prevIP = runtime.Network.RouterIP
		prevMAC = runtime.Network.RouterMAC
	}
	currIP, currMAC := "", ""
	if network != nil {
		currIP = network.RouterIP
		currMAC = network.RouterMAC
	}
	if currIP != prevIP || currMAC != prevMAC {
		lbl := "no network"
		if network != nil {
			lbl = network.Label()
		}
		fired = append(fired, firedEv{core.TriggerEvent{Kind: "networkChanged"}, lbl})
	}

	var events []core.BIREvent
	for _, f := range fired {
		var matching []core.TriggerRule
		var notes []string
		notifyAny := false
		for _, t := range cfg.Triggers {
			if !t.Enabled || !t.Event.Equal(f.ev) {
				continue
			}
			matching = append(matching, t)
			if t.Notify {
				notifyAny = true
			}
			for _, a := range t.Actions {
				switch a.Kind {
				case "activateProfile":
					runtime.ProfileOverride = a.ProfileID
					runtime.ProfileCleared = false
					pName := "?"
					for _, p := range cfg.Profiles {
						if p.ID == a.ProfileID {
							pName = p.Name
							break
						}
					}
					notes = append(notes, "profile → "+pName)
				case "clearProfile":
					runtime.ProfileOverride = ""
					runtime.ProfileCleared = true
					notes = append(notes, "profile cleared")
				case "killSwitchOn":
					v := true
					runtime.KillSwitchOverride = &v
					notes = append(notes, "kill switch on")
				case "killSwitchOff":
					v := false
					runtime.KillSwitchOverride = &v
					notes = append(notes, "kill switch off")
				case "killSwitchAuto":
					runtime.KillSwitchOverride = nil
					notes = append(notes, "kill switch back to its setting")
				case "learnVPNServers":
					learned := learnVPNServers()
					notes = append(notes, fmt.Sprintf("%d VPN servers known", len(learned)))
				}
			}
		}
		var parts []string
		if f.detail != "" {
			parts = append(parts, f.detail)
		}
		if len(matching) > 0 {
			var names []string
			for _, m := range matching {
				names = append(names, m.Name)
			}
			parts = append(parts, "triggers: "+strings.Join(names, ", "))
		}
		parts = append(parts, notes...)
		events = append(events, core.BIREvent{
			ID:     newUUID(),
			Date:   time.Now().UTC(),
			Kind:   f.ev.Kind,
			Title:  f.ev.Label(),
			Detail: strings.Join(parts, " · "),
			Notify: notifyAny,
		})
	}
	return events
}

func apply(trigger string) {
	ensureDirs()
	prevStatus, hasPrevStatus := core.LoadJSON[core.HelperStatus](core.StatusFile)
	automation, _ := core.LoadJSON[core.AutomationConfig](core.AutomationFile)
	loadedRuntime, hasRuntime := core.LoadJSON[core.RuntimeState](core.RuntimeFile)
	runtime := loadedRuntime
	if runtime.InterfacesUp == nil {
		runtime.InterfacesUp = map[string]bool{}
	}

	interfaces := core.SnapshotInterfaces()
	table := core.RouteTable("inet")
	events := detectEvents(automation, &runtime, !hasRuntime, interfaces, table)

	var profile *core.RoutingProfile
	reason := ""
	if runtime.ProfileCleared {
		reason = "cleared by a trigger"
	} else {
		profile, reason = automation.EffectiveProfile(runtime.ProfileOverride, runtime.Network)
	}
	profileID := ""
	profileName := ""
	if profile != nil {
		profileID = profile.ID
		profileName = profile.Name
	}
	if hasRuntime && profileID != runtime.ActiveProfileID {
		titleName := "none"
		if profileName != "" {
			titleName = profileName
		}
		events = append(events, core.BIREvent{
			ID:     newUUID(),
			Date:   time.Now().UTC(),
			Kind:   "profileChanged",
			Title:  "Profile: " + titleName,
			Detail: reason,
		})
	}
	runtime.ActiveProfileID = profileID

	allRules := loadRules()
	profileSet := map[string]bool{}
	if profile != nil {
		for _, id := range profile.RuleIDs {
			profileSet[id] = true
		}
	}
	rules := make([]core.RouteRule, len(allRules))
	for i, r := range allRules {
		if profile != nil && !profileSet[r.ID] {
			r.Enabled = false
		}
		rules[i] = r
	}

	cache, _ := core.LoadJSON[map[string][]string](core.DNSCache)
	if cache == nil {
		cache = map[string][]string{}
	}
	previous, _ := core.LoadJSON[[]core.ManagedRoute](core.AppliedState)

	plan := core.PlanRoutes(
		rules,
		interfaces,
		core.RouterOf,
		func(h string) []string { return core.ResolveIPv4(h, 4*time.Second) },
		cache,
	)
	for k, v := range plan.Cache {
		cache[k] = v
	}

	desiredKeys := map[string]bool{}
	for _, r := range plan.Routes {
		desiredKeys[r.Key()] = true
	}
	liveRules := map[string]bool{}
	for _, r := range rules {
		if r.Enabled {
			liveRules[r.ID] = true
		}
	}

	// 1. Remove old routes no longer wanted.
	for _, old := range previous {
		if desiredKeys[old.Key()] {
			continue
		}
		if liveRules[old.RuleID] && plan.Statuses[old.RuleID].State == core.StateWaiting {
			continue
		}
		_ = removeRoute(old, "no longer in any active rule", table)
	}

	// 2. Install or repair desired routes.
	var installed []core.ManagedRoute
	for _, old := range previous {
		if !desiredKeys[old.Key()] && liveRules[old.RuleID] && plan.Statuses[old.RuleID].State == core.StateWaiting {
			installed = append(installed, old)
		}
	}

	for _, desired := range plan.Routes {
		st := plan.Statuses[desired.RuleID]
		if core.FindInstalledRoute(desired, table) {
			installed = append(installed, desired)
			st.Routes = append(st.Routes, fmt.Sprintf("%s → %s", desired.DestinationLabel(), desired.NextHopLabel()))
			plan.Statuses[desired.RuleID] = st
			continue
		}
		r := ipCmd(core.RouteReplaceArgs(desired)...)
		if r.OK() {
			installed = append(installed, desired)
			st.Routes = append(st.Routes, fmt.Sprintf("%s → %s", desired.DestinationLabel(), desired.NextHopLabel()))
			dom := ""
			if desired.SourceDomain != "" {
				dom = " [" + desired.SourceDomain + "]"
			}
			logMsg(fmt.Sprintf("added %s -> %s%s", desired.DestinationLabel(), desired.NextHopLabel(), dom))
		} else {
			st.Messages = append(st.Messages, fmt.Sprintf("ip route replace %s failed: %s", desired.DestinationLabel(), r.Combined()))
			logMsg(fmt.Sprintf("failed to add %s: %s", desired.DestinationLabel(), r.Combined()))
		}
		plan.Statuses[desired.RuleID] = st
	}

	var ordered []core.RuleStatus
	for _, rule := range allRules {
		st, ok := plan.Statuses[rule.ID]
		if !ok {
			continue
		}
		if rule.Enabled && profile != nil && !profileSet[rule.ID] {
			st.Messages = append(st.Messages, fmt.Sprintf("Not part of profile “%s”", profile.Name))
		}
		st.Finalize()
		ordered = append(ordered, st)
	}

	// 3. Kill switch & block list.
	var ksOverride *bool
	if runtime.KillSwitchOverride != nil {
		ksOverride = runtime.KillSwitchOverride
	} else if profile != nil && profile.KillSwitch != nil {
		ksOverride = profile.KillSwitch
	}
	ks, bl := applyFirewall(plan.Routes, interfaces, cache, ksOverride, automation.Blocked)
	armed := ks != nil && ks.Active
	if runtime.KillSwitchArmed != nil && *runtime.KillSwitchArmed != armed {
		kind := "killSwitchDisarmed"
		title := "Kill switch off"
		if armed {
			kind = "killSwitchArmed"
			title = "Kill switch armed"
		}
		events = append(events, core.BIREvent{ID: newUUID(), Date: time.Now().UTC(), Kind: kind, Title: title})
	}
	runtime.KillSwitchArmed = &armed

	// 4. Per-domain DNS & blocked domains.
	var blockedDomains []string
	for _, b := range automation.Blocked {
		if !b.Enabled {
			continue
		}
		if kind, val, ok := core.ParseKSEntry(b.Value); ok && kind == "domain" {
			blockedDomains = append(blockedDomains, val)
		}
	}
	resolvers := applyResolvers(automation.Resolvers, blockedDomains)

	tunnelUp := runtime.TunnelUp
	status := core.HelperStatus{
		UpdatedAt:       time.Now().UTC(),
		Trigger:         trigger,
		HelperVersion:   core.Version,
		Rules:           ordered,
		InstalledRoutes: installed,
		KillSwitch:      ks,
		Profile:         &core.ActiveProfileInfo{ID: profileID, Name: profileName, Reason: reason},
		Resolvers:       &resolvers,
		BlockList:       bl,
		TunnelUp:        &tunnelUp,
	}

	for _, st := range ordered {
		if st.State != core.StateFailed && st.State != core.StatePartial {
			continue
		}
		already := false
		if hasPrevStatus {
			for _, ps := range prevStatus.Rules {
				if ps.RuleID == st.RuleID && ps.State == st.State {
					already = true
					break
				}
			}
		}
		if !already {
			name := "rule"
			for _, r := range allRules {
				if r.ID == st.RuleID {
					name = r.Name
					break
				}
			}
			verb := "is only partly applied"
			if st.State == core.StateFailed {
				verb = "failed"
			}
			det := ""
			if len(st.Messages) > 0 {
				det = st.Messages[0]
			}
			events = append(events, core.BIREvent{
				ID:     newUUID(),
				Date:   time.Now().UTC(),
				Kind:   "ruleFailed",
				Title:  fmt.Sprintf("Rule “%s” %s", name, verb),
				Detail: det,
			})
		}
	}

	if installed == nil {
		installed = []core.ManagedRoute{}
	}
	if err := core.SaveJSON(installed, core.AppliedState, 0o644); err != nil {
		fail("could not write applied state: "+err.Error(), 1)
	}
	if err := core.SaveJSON(status, core.StatusFile, 0o644); err != nil {
		fail("could not write status: "+err.Error(), 1)
	}
	_ = core.SaveJSON(cache, core.DNSCache, 0o644)
	_ = core.SaveJSON(runtime, core.RuntimeFile, 0o644)

	recordEvents(events)
	pLabel := "none"
	if profileName != "" {
		pLabel = profileName
	}
	ksLabel := "off"
	if armed {
		ksLabel = "on"
	}
	if trigger != "daemon" && trigger != "timer" {
		logMsg(fmt.Sprintf("apply (%s): %d/%d routes in place; profile %s; kill switch %s", trigger, len(installed), len(plan.Routes), pLabel, ksLabel))
	}
	fmt.Printf("ok: %d/%d routes; profile %s; kill switch %s\n", len(installed), len(plan.Routes), pLabel, ksLabel)
}

func removeAll() {
	previous, _ := core.LoadJSON[[]core.ManagedRoute](core.AppliedState)
	table := core.RouteTable("inet")
	removed := 0
	for _, old := range previous {
		if removeRoute(old, "remove-all", table) {
			removed++
		}
	}
	killSwitchOff("remove-all")
	_ = applyResolvers(nil, nil)
	_ = os.Remove(core.RuntimeFile)
	ensureDirs()
	_ = core.SaveJSON([]core.ManagedRoute{}, core.AppliedState, 0o644)
	_ = core.SaveJSON(core.HelperStatus{
		UpdatedAt:       time.Now().UTC(),
		Trigger:         "remove-all",
		HelperVersion:   core.Version,
		Rules:           []core.RuleStatus{},
		InstalledRoutes: []core.ManagedRoute{},
	}, core.StatusFile, 0o644)
	fmt.Printf("ok: removed %d routes, kill switch off\n", removed)
}

func runDaemon(interval int) {
	logMsg(fmt.Sprintf("daemon started (version %s, every %ds and on netlink network changes)", core.Version, interval))
	withLock(func() { apply("boot") })

	var mu sync.Mutex
	var pendingTimer *time.Timer
	schedule := func(reason string, delay time.Duration) {
		mu.Lock()
		defer mu.Unlock()
		if pendingTimer != nil {
			pendingTimer.Stop()
		}
		pendingTimer = time.AfterFunc(delay, func() {
			withLock(func() { apply(reason) })
		})
	}

	// Watch Linux netlink events via `ip monitor route link address`.
	go func() {
		for {
			cmd := exec.Command("ip", "monitor", "route", "link", "address")
			stdout, err := cmd.StdoutPipe()
			if err == nil && cmd.Start() == nil {
				scanner := bufio.NewScanner(stdout)
				for scanner.Scan() {
					line := scanner.Text()
					if strings.TrimSpace(line) != "" {
						schedule("network change", 2*time.Second)
					}
				}
				_ = cmd.Wait()
			}
			time.Sleep(5 * time.Second)
		}
	}()

	hupCh := make(chan os.Signal, 1)
	signal.Notify(hupCh, syscall.SIGHUP)
	termCh := make(chan os.Signal, 1)
	signal.Notify(termCh, syscall.SIGINT, syscall.SIGTERM)

	ticker := time.NewTicker(time.Duration(maxInt(15, interval)) * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			schedule("timer", 0)
		case <-hupCh:
			schedule("reload", 0)
		case sig := <-termCh:
			logMsg("daemon stopping (" + sig.String() + ")")
			return
		}
	}
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

var _ = filepath.Base

