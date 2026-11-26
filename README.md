# SplitRoute

**Native Linux per-destination routing, VPN split tunneling, per-domain DNS, and isolated `nftables` kill switch.**

Built with **GTK4 + Libadwaita** (Obsidian & Emerald theme) and a privileged **Go + Netlink + `systemd`** root helper, **SplitRoute** lets you choose how traffic leaves your Linux machine one IP, CIDR, or domain at a time — without ever modifying your VPN client's configuration.

---

## Why SplitRoute?

When using a full-tunnel VPN (`ppp0`, `tun0`, `wg0`, `xray`, `sing-box`, OpenVPN, etc.), all traffic — including internal company servers, local services, or specific domains — gets forced into the VPN tunnel. **SplitRoute** solves this cleanly at the kernel routing table and `nftables` level:

- Send internal portals or specific domains straight out your Ethernet/Wi-Fi router past a full-tunnel VPN.
- Route specific corporate subnets through a secondary site VPN.
- Route internal domains (e.g. `*.corp.local`, `*.company.com`) to internal DNS servers via `systemd-resolved` without breaking public DNS.
- Enforce a kill switch or global block list in an isolated `nftables` table (`inet splitroute`) so `ufw`, `firewalld`, `docker`, and system rules are never touched.

---

## Features

| Module | Capabilities |
|---|---|
| **Dashboard** | Public IPv4 & IPv6 with country flag, city/region, ISP/ASN, timezone, and coordinates; per-path public IP checks; local IP, internet egress interface, rule health, and live throughput per adapter |
| **Routing Rules** | Route IPv4 hosts, CIDR networks, or domain names via an interface's DHCP router, directly on an interface, or via a custom gateway. Supports persistent rules (across reboots) and until-reboot session rules |
| **Kill Switch (`nftables`)** | Isolated `table inet splitroute` supporting **Block selected services** (with updatable Telegram CIDR preset & custom lists) and **Block everything except allowed** (with auto-detection of VPN server peers for `xray`, `sing-box`, `v2ray`, `openvpn`, `wireguard`, etc.) |
| **Block List** | Completely blocks IPs, IPv4/IPv6 CIDRs, and domains across all interfaces (VPN included) via `nftables` + `systemd-resolved` + `/etc/hosts` |
| **Routing Table** | Live IPv4 & IPv6 kernel routing table (`ip -j route`), filtering, route ownership badges, flag legend, one-off route add/delete, and static route importer |
| **Path Check** | Enter any IP or domain to inspect DNS answers, kernel FIB route (`ip route get`), matching rule, kill switch/block list verdict, TCP 443 latency, and live `traceroute` |
| **DNS** | `bash.ws` DNS leak test, active `resolvectl` resolver audit with outside-tunnel leak warnings, and per-domain DNS routing (`srdns*` dummy links) |
| **Network Adapters** | Addresses, MAC, gateway, DNS, 64-bit `/proc/net/dev` traffic counters, friendly aliases, interface up/down, Wi-Fi radio toggle, DHCP renewal, MTU, DNS settings, and DNS cache flush |
| **Connections & Traffic** | Live per-process socket monitor (`ss -tunap`) highlighting connections leaking outside the VPN tunnel, plus 15m / 1h / 24h / 7d throughput charts |
| **Speed Test** | Cloudflare & LibreSpeed multi-stream speed test with 270° logarithmic speedometer dial, sparkline, ping/jitter, latency under load (bufferbloat grade), and per-interface pinning via `splitroute-probe` (`SO_BINDTODEVICE`) |
| **Profiles & Automation** | Named rule profiles activated manually, by router IP/MAC fingerprint, or on Netlink VPN/interface/network events |
| **Persistence & Backup** | Root `systemd` daemon (`splitroute-helper.service`) with Netlink `ip monitor` + periodic reconciliation, JSON export/import, automatic snapshots, and one-click Reset to Default |
| **System Tray** | Native `StatusNotifierItem` (`Icon`, `Country flag`, `Flag + IP`, or `↓/↑ Speed`) with quick status popup window and background operation when closed |

---

## Architecture

```text
SplitRoute GUI (Python 3 + GTK4 + Libadwaita)       splitroute-helper (Go, runs as root)
 ├─ Edits draft workspace (~/.config/splitroute)     ├─ Watches Netlink (ip monitor) + timer + SIGHUP
 ├─ Apply ──► pkexec (single PolicyKit prompt) ────► ├─ Resolves domains & installs ip route rules
 └─ Reads live status (/run/splitroute/status.json)  ├─ Loads atomic nftables rules (table inet splitroute)
                                                     └─ Configures per-domain systemd-resolved links
splitroute-probe (Go + cap_net_raw=ep)
 └─ Binds HTTP probes & speed tests to any interface via SO_BINDTODEVICE without root prompts
```

---

## Requirements

Tested on **Ubuntu 24.04 LTS** (compatible with modern systemd-based Linux distributions):

- **Go** `1.22+`
- **Python** `3.10+` with `PyGObject` & `PyCairo` (`python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`)
- Standard Linux networking utilities: `iproute2` (`ip`, `ss`), `nftables` (`nft`), `polkitd` (`pkexec`), `network-manager` (`nmcli`), `systemd-resolved` (`resolvectl`), `traceroute`

On Ubuntu / Debian:
```bash
sudo apt install golang-go python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
  iproute2 nftables network-manager policykit-1 traceroute libcap2-bin
```

---

## Build & Install

```bash
git clone https://github.com/lord-amin/split-router.git
cd split-router

# 1. Build Go binaries and verify GTK4/Libadwaita runtime
./scripts/build.sh

# 2. Install desktop launcher & icon for the current user
./scripts/install.sh

# 3. Launch SplitRoute
./scripts/run.sh
```

Inside the app, open **Setup Assistant** (or **Persistence**) to install `/usr/local/libexec/splitroute-helper`, `/usr/local/libexec/splitroute-probe` (`cap_net_raw=ep`), and `splitroute-helper.service` with a single PolicyKit password prompt.

You can also install system binaries directly via:
```bash
./scripts/install.sh --system
```

---

## Diagnostics & Testing

Non-destructive diagnostics (no root required):
```bash
./scripts/run.sh --selftest
./build/splitroute-helper plan /path/to/rules.json
```

Run unit tests:
```bash
go test -v ./...
python3 -m unittest discover -s tests -v
```

---

## Security Model

- `splitroute-helper` executes system tools using argument arrays (`exec.Command`), never invoking a shell.
- Interface names, IPv4/IPv6 addresses, CIDRs, and domains are strictly validated before execution.
- Persistent configs are stored root-owned under `/etc/splitroute/`; applying changes always requires PolicyKit (`pkexec`) authorization.
- Firewall rules live exclusively inside `table inet splitroute` and never alter existing `ufw`, `iptables`, or `nftables` tables.

---

## Author & License

Created and maintained by **lord-amin** ([GitHub](https://github.com/lord-amin) · `sadeghi.yaser@gmail.com`).  
Licensed under the **MIT License** — see [LICENSE](LICENSE).
