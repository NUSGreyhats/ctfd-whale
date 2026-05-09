# Changelog

## 2026-05-09 Security Patch

- Fix rate limiting bypass: the 60-second cooldown was stored in the session cookie, so clearing cookies or using incognito bypassed it entirely. Container action cooldowns are now enforced server-side in cache per user or team, and locks are released with try/finally on every path.
- Fix team-mode quota/isolation bypass: in team mode, container lookup, create, renew, remove, and flag checks are now scoped to the team instead of the individual user so teammates cannot spawn parallel instances or bypass per-team limits.
- Fix incomplete challenge access checks: container API requests now validate `challenge_id` as an integer, enforce CTF time/email decorators when available, require a team in team mode, and enforce challenge prerequisites before allowing instance access or creation.
- Fix static flag bypass for dynamic Docker challenges: normal CTFd flags no longer override the per-container dynamic flag, preventing competitors from sharing one static flag across instances.
- Fix container lifecycle races and orphaned services: the global lock now covers database record creation/removal, Docker service operations, and router reloads; containers have explicit `creating`, `running`, and `removing` states; router reloads only include active containers.
- Fix max-container-count off-by-one: the configured maximum is now enforced with `>=` instead of allowing one extra container.
- Fix unsafe cleanup sequencing: Docker service removal is attempted even when router unregister fails, failed removals remain tracked for retry, and direct-mode ports are returned to the pool only after the route is removed.
- Fix race condition in FilesystemCacheProvider port/network allocation: concurrent requests could receive the same port, routing one competitor's traffic to another's container. All get/pop/set sequences are now protected by a threading lock, and per-user and global locks are properly implemented instead of no-op stubs.
- Fix missing challenge type validation: competitors could request container creation for non-docker challenges, causing unhandled exceptions that destroyed their existing running container and leaked stack traces. The challenge_visible decorator now rejects non-docker challenges.
- Fix timing side-channel on flag comparison: Python's `==` short-circuits on the first mismatched byte, leaking flag characters via response time differences. Flag comparison now uses `hmac.compare_digest()`.
- Fix remaining time calculation: `timedelta.seconds` only returns the seconds component (0-86399), ignoring the days component. Replaced with `total_seconds()` to prevent expired containers from appearing valid.
- Fix path traversal in admin template include: the `view_mode` query parameter was used directly in a Jinja2 `{% include %}` path with no validation. Now restricted to an allowlist of `list` and `card`.
- Reduce template injection risk: Whale's configurable Jinja templates now render through a sandboxed environment, generated HTTP subdomains are strictly validated as DNS labels, and overlong generated flags are rejected.
- Reduce frontend XSS risk: API error messages and LAN-domain values are escaped/rendered as text instead of raw HTML.
- Add Docker service hardening defaults for new challenge services: init process enabled, `NET_RAW` dropped by default, log size/file limits added, restart condition defaults to `none`, and optional read-only root filesystem and run-as-user settings are exposed in the admin UI.
- Harden the example FRP deployment: the sample `frpc` admin API no longer binds to `0.0.0.0`, uses a CTFd-only internal address, and replaces weak demo credentials with explicit placeholders.

## 2020-03-18

- Allow non-dynamic flag.

## 2020-02-18

- Refine front for ctfd newer version.(@frankli0324)

## 2019-11-21

- Add network prefix & timeout setting.
- Refine port and network range search
- Refine frp request
- Refine lock timeout

## 2019-11-08

- Add Lan Domain

## 2019-11-04

- Change backend to Docker Swarm.
- Support depoly different os image to different os node.

You should init docker swarm, and add your node to it. And name them with following command:

```
docker node update --label-add name=windows-1 ****
docker node update --label-add name=linux-1 ****
```

Name of them should begin with windows- or linux-.

And put them in the setting panel.

Then if you want to deploy a instance to windows node, You should tag your name with prefix "windows", like "glzjin/super_sql:windows".

And please modify the container network driver to 'Overlay'!

## 2019-10-30

- Optimize for multi worker.
- Try to fix concurrency request problem.

Now You should set the redis with REDIS_HOST environment varible.

## 2019-09-26

- Add frp http port setting.

You should config it at the settings for http redirect.

## 2019-09-15

- Add Container Network Setting and DNS Setting.

Now You can setup a DNS Server in your Container Network.
- For single-instance network, Just connect your dns server to it and input the ip address in the seeting panel.
- For multi-instance network, You should rename the dns server to a name include "dns", than add it to auto connect instance. It will be used as a dns server.

## 2019-09-14

- Refine plugin path.

## 2019-09-13

- Refine removal.

## 2019-08-29

- Add CPU usage limit.
- Allow the multi-image challenge.

Upgrade:
1. Execute this SQL in ctfd database.

```
alter table dynamic_docker_challenge add column cpu_limit float default 0.5 after memory_limit;
```  

2. Setting the containers you want to plugin to a single multi-image network. (In settings panel)

3. When you create a challenge you can set the docker image like this

```
{"socks": "serjs/go-socks5-proxy", "web": "blog_revenge_blog", "mysql": "blog_revenge_mysql", "oauth": "blog_revenge_oauth"}
```

The first one will be redirected the traffic.
