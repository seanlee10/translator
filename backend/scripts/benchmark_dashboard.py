"""Render results.json as a self-contained HTML dashboard.

Separate from the benchmark run so the page can be re-rendered without
re-measuring anything. Everything is inlined: the artifact viewer blocks
requests to any external host, so a CDN stylesheet or font would fail
silently and leave the page unstyled.
"""

import argparse
import html
import json
import statistics
from pathlib import Path

# Three categorical hues, chosen to stay distinguishable on both grounds and
# to be nobody's brand colour — the point is to compare, not to advertise.
PALETTE = {
    "anthropic": ("#C05A2E", "#E0834F"),
    "groq": ("#1F7A8C", "#3FA6BB"),
    "xai": ("#6A5AA8", "#9285D4"),
}
FALLBACK = ("#5C6771", "#98A4AF")

CSS = """
:root {
  --ground:#F6F7F8; --surface:#FFFFFF; --surface-2:#FBFCFC;
  --ink:#12171C; --ink-muted:#5C6771; --ink-faint:#8B959E;
  --line:#E2E6EA; --line-strong:#CFD6DC;
  --good:#2E7350; --warn:#A8770F; --bad:#AA3F35;
  --shadow:0 1px 2px rgba(18,23,28,.05), 0 8px 24px rgba(18,23,28,.05);
  --ui:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --ko:"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic","Nanum Gothic",
       system-ui,sans-serif;
  --zh:"PingFang TC","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground:#0E1216; --surface:#161C22; --surface-2:#1B222A;
    --ink:#E8EDF1; --ink-muted:#98A4AF; --ink-faint:#6B7783;
    --line:#263039; --line-strong:#33404B;
    --good:#5FBF8C; --warn:#D9A63C; --bad:#E0776A;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
  }
}
:root[data-theme="dark"] {
  --ground:#0E1216; --surface:#161C22; --surface-2:#1B222A;
  --ink:#E8EDF1; --ink-muted:#98A4AF; --ink-faint:#6B7783;
  --line:#263039; --line-strong:#33404B;
  --good:#5FBF8C; --warn:#D9A63C; --bad:#E0776A;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
}

* { box-sizing:border-box; }
body {
  margin:0; background:var(--ground); color:var(--ink);
  font-family:var(--ui); font-size:15px; line-height:1.55;
  -webkit-font-smoothing:antialiased;
}
.wrap { max-width:1180px; margin:0 auto; padding:40px 24px 72px; }

/* ---- header: run metadata as a console strip ---- */
header { margin-bottom:36px; }
h1 {
  font-size:clamp(26px,4vw,38px); line-height:1.1; margin:0 0 6px;
  letter-spacing:-.022em; font-weight:660; text-wrap:balance;
}
.sub { color:var(--ink-muted); margin:0 0 20px; max-width:62ch; }
.meta {
  display:flex; flex-wrap:wrap; gap:0; border:1px solid var(--line);
  border-radius:8px; overflow:hidden; background:var(--surface);
}
.meta div {
  padding:9px 14px; border-right:1px solid var(--line);
  font-family:var(--mono); font-size:12px; color:var(--ink-muted);
  font-variant-numeric:tabular-nums;
}
.meta div:last-child { border-right:0; }
.meta b { color:var(--ink); font-weight:600; }

/* ---- section ---- */
section { margin-top:40px; }
h2 {
  font-size:12px; text-transform:uppercase; letter-spacing:.1em;
  color:var(--ink-faint); font-weight:640; margin:0 0 14px;
}

/* ---- model cards ---- */
.cards { display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); }
.card {
  background:var(--surface); border:1px solid var(--line); border-radius:10px;
  padding:18px 18px 16px; box-shadow:var(--shadow); position:relative;
  border-top:3px solid var(--hue);
}
.card .name { font-weight:640; font-size:16px; letter-spacing:-.01em; }
.card .model {
  font-family:var(--mono); font-size:11.5px; color:var(--ink-faint);
  margin-top:2px; word-break:break-all;
}
.figures { display:flex; gap:22px; margin-top:16px; }
.fig .k {
  font-size:10.5px; text-transform:uppercase; letter-spacing:.08em;
  color:var(--ink-faint); font-weight:600;
}
.fig .v {
  font-family:var(--mono); font-size:24px; font-weight:600;
  font-variant-numeric:tabular-nums; letter-spacing:-.02em; line-height:1.2;
}
.fig .v small { font-size:12px; color:var(--ink-muted); font-weight:500; }
.badges { display:flex; gap:6px; flex-wrap:wrap; margin-top:14px; }
.badge {
  font-size:11px; font-weight:600; padding:3px 8px; border-radius:999px;
  border:1px solid var(--line-strong); color:var(--ink-muted);
  font-family:var(--mono); font-variant-numeric:tabular-nums;
}
.badge.win { color:var(--good); border-color:color-mix(in srgb,var(--good) 40%,transparent); }
.badge.fail { color:var(--bad); border-color:color-mix(in srgb,var(--bad) 40%,transparent); }

/* ---- comparison bars ---- */
.bars { display:grid; gap:10px; }
.bar-row { display:grid; grid-template-columns:96px 1fr 72px; align-items:center; gap:12px; }
.bar-row .lab { font-size:13px; font-weight:560; }
.track {
  height:22px; background:var(--surface-2); border:1px solid var(--line);
  border-radius:5px; overflow:hidden; position:relative;
}
.fill { height:100%; background:var(--hue); opacity:.85; }
.fill.ghost { background:var(--hue); opacity:.3; position:absolute; top:0; left:0; }
.bar-row .num {
  font-family:var(--mono); font-size:13px; text-align:right;
  font-variant-numeric:tabular-nums; color:var(--ink-muted);
}
.legend { display:flex; gap:16px; margin-top:12px; font-size:12px; color:var(--ink-muted); }
.legend span { display:flex; align-items:center; gap:6px; }
.swatch { width:11px; height:11px; border-radius:3px; background:var(--ink-muted); }
.swatch.solid { opacity:.85; } .swatch.ghost { opacity:.3; }

/* ---- phrase table ---- */
.scroll { overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:var(--surface); }
table { border-collapse:collapse; width:100%; min-width:900px; font-size:13.5px; }
th {
  text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:.08em;
  color:var(--ink-faint); font-weight:640; padding:11px 14px;
  border-bottom:1px solid var(--line); background:var(--surface-2);
  position:sticky; top:0; white-space:nowrap;
}
th.hue { border-bottom:2px solid var(--hue); }
td { padding:12px 14px; border-bottom:1px solid var(--line); vertical-align:top; }
tr:last-child td { border-bottom:0; }
td.en { color:var(--ink); max-width:290px; }
td.ko { font-family:var(--ko); max-width:270px; }
td.ko.zh { font-family:var(--zh); }
td.ko.best { background:color-mix(in srgb,var(--good) 9%,transparent); }
.tick {
  display:inline-flex; align-items:center; gap:5px; margin-top:5px;
  font-family:var(--mono); font-size:11px; color:var(--ink-faint);
  font-variant-numeric:tabular-nums;
}
.tick .sc { color:var(--ink-muted); }
.tick .w { color:var(--good); font-weight:700; }
.stamp {
  font-family:var(--mono); font-size:11px; color:var(--ink-faint);
  font-variant-numeric:tabular-nums; white-space:nowrap;
}
.err { color:var(--bad); font-family:var(--mono); font-size:11.5px; }
.note { color:var(--ink-muted); font-size:12.5px; margin-top:6px; font-style:italic; }
.alert {
  margin-top:14px; padding:12px 14px; border-radius:8px; font-size:13px;
  color:var(--ink); background:color-mix(in srgb,var(--warn) 10%,transparent);
  border:1px solid color-mix(in srgb,var(--warn) 38%,transparent);
}
.alert b { font-weight:640; }
.badge.warnb { color:var(--warn); border-color:color-mix(in srgb,var(--warn) 45%,transparent); }
footer {
  margin-top:44px; padding-top:18px; border-top:1px solid var(--line);
  color:var(--ink-faint); font-size:12.5px;
}
footer code { font-family:var(--mono); font-size:11.5px; }
@media (max-width:640px){ .figures{gap:16px} .bar-row{grid-template-columns:74px 1fr 64px} }
"""


def throttle(rows, name):
    """Detect a provider that got rate-limited partway through the run.

    A throttled provider is indistinguishable from a slow model if you only
    read the aggregate: its p50 doubles and stays doubled. What separates
    them is the shape — a real model's latency is stationary, a throttled
    one steps up at a point and never comes back down. Comparing the two
    halves of the run catches exactly that step.

    Reported, never silently corrected: the numbers stay as measured and the
    page says the measurement is suspect.
    """
    lat = [
        r["translations"][name]["latency_ms"]
        for r in rows
        if r["translations"].get(name, {}).get("ko")
    ]
    if len(lat) < 12:
        return None
    half = len(lat) // 2
    early = statistics.median(lat[:half])
    late = statistics.median(lat[half:])
    if late >= early * 2 and late - early > 300:
        return {"early": round(early), "late": round(late)}
    return None


def hue_tokens(providers) -> str:
    """Per-provider hues as tokens, defined for all three theme states.

    Not inline styles with a media query beside them: a rule inside an
    element-level <style> that no selector matches is dead CSS, and a colour
    whose only definition lives in a media block never applies in the
    un-stamped "system" theme, which is what most viewers get.
    """
    light = " ".join(
        f"--hue-{n}:{PALETTE.get(n, FALLBACK)[0]};" for n in providers
    )
    dark = " ".join(
        f"--hue-{n}:{PALETTE.get(n, FALLBACK)[1]};" for n in providers
    )
    return (
        f":root{{{light}}}"
        f"@media (prefers-color-scheme:dark){{:root:not([data-theme=\"light\"])"
        f"{{{dark}}}}}"
        f":root[data-theme=\"dark\"]{{{dark}}}"
    )


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def num(value, suffix="", dash="—"):
    return f"{value}{suffix}" if value is not None else dash


def render(results: dict) -> str:
    summary = results["summary"]
    providers = list(summary)
    rows = results["rows"]
    models = results.get("models", {})
    # This script deliberately does not import app.direction — it only ever
    # reads results.json, so it can re-render without the app package or a
    # working directory. Same directions, small local label table instead.
    # Note results.json's "en"/"ko" row keys are ROLE keys (spoken /
    # translated), not languages.
    direction = results.get("direction", "en>ko")
    source_label, target_label = {
        "en>ko": ("english", "korean"),
        "ko>en": ("korean", "english"),
        "en>zh": ("english", "traditional chinese"),
    }.get(direction, ("english", "korean"))
    target_cls = " zh" if direction == "en>zh" else ""

    flags = {n: throttle(rows, n) for n in providers}
    judged_any = any(s["judged"] for s in summary.values())
    best_quality = max(
        (p for p in providers if summary[p]["quality"]["overall"] is not None),
        key=lambda p: summary[p]["quality"]["overall"], default=None,
    )
    best_latency = min(
        (p for p in providers if summary[p]["latency_ms"]["p50"] is not None),
        key=lambda p: summary[p]["latency_ms"]["p50"], default=None,
    )
    worst_p95 = max(
        (summary[p]["latency_ms"]["p95"] or 0 for p in providers), default=1
    ) or 1

    out = [
        "<title>Commencement, Translated Live</title>"
        f"<style>{CSS}{hue_tokens(providers)}</style>"
    ]
    out.append('<div class="wrap"><header>')
    out.append("<h1>Translating a commencement address, live</h1>")
    out.append(
        f'<p class="sub">Deepgram transcribed the audio once; every model then '
        f"translated that identical phrase list into {esc(target_label.title())}, each "
        f"carrying its own rolling context exactly as it would in a live "
        f"session. Quality comes from a blind three-way comparison where the "
        f"models were relabelled A/B/C.</p>"
    )
    audio_name = Path(results.get("audio", "")).name
    minutes = results.get("minutes")
    out.append('<div class="meta">')
    out.append(f'<div>direction <b>{esc(results.get("direction", "en>ko"))}</b></div>')
    out.append(f'<div>source <b>{esc(audio_name)}</b></div>')
    out.append(
        f'<div>span <b>{esc(f"first {minutes:g} min" if minutes else "full")}</b></div>'
    )
    out.append(f'<div>phrases <b>{results["phrases"]}</b></div>')
    out.append(f'<div>register <b>{esc(results.get("korean_register"))}</b></div>')
    if judged_any:
        out.append(f'<div>judge <b>{esc(results.get("judge_model"))}</b></div>')
    out.append(f'<div>run <b>{esc(results.get("run_id"))}</b></div>')
    out.append("</div></header>")

    # ---- model cards ----
    judge_provider = results.get("judge_provider")
    if judged_any and judge_provider in providers:
        out.append(
            f'<div class="alert" style="margin-top:18px"><b>The judge is also '
            f'one of the contestants.</b> {esc(judge_provider)} scored its own '
            f"translations here, so its quality numbers are not impartially "
            f"ranked and should not be read as a verdict against the others. "
            f"Candidates were shown as shuffled A/B/C with no model names, so "
            f"the judge could not knowingly favour itself — but a stylistic "
            f"self-preference is not ruled out. A judge from outside the "
            f"compared set is the fix.</div>"
        )
    out.append("<section><h2>By model</h2><div class=\"cards\">")
    for name in providers:
        s = summary[name]
        q = s["quality"]["overall"]
        out.append(f'<div class="card" style="--hue:var(--hue-{name})">')
        out.append(f'<div class="name">{esc(name)}</div>')
        out.append(f'<div class="model">{esc(models.get(name, ""))}</div>')
        out.append('<div class="figures">')
        out.append(
            '<div class="fig"><div class="k">latency p50</div>'
            f'<div class="v">{num(s["latency_ms"]["p50"])}'
            "<small> ms</small></div></div>"
        )
        out.append(
            '<div class="fig"><div class="k">p95</div>'
            f'<div class="v">{num(s["latency_ms"]["p95"])}'
            "<small> ms</small></div></div>"
        )
        if judged_any:
            out.append(
                '<div class="fig"><div class="k">quality</div>'
                f'<div class="v">{num(q)}<small> / 5</small></div></div>'
            )
        out.append("</div>")

        out.append('<div class="badges">')
        if judged_any and s["win_rate"] is not None:
            cls = " win" if name == best_quality else ""
            out.append(
                f'<span class="badge{cls}">wins {s["wins"]}/{s["judged"]}'
                f' · {s["win_rate"] * 100:.0f}%</span>'
            )
        if name == best_latency:
            out.append('<span class="badge win">fastest</span>')
        if s["failures"]:
            out.append(f'<span class="badge fail">{s["failures"]} failed</span>')
        if flags.get(name):
            out.append('<span class="badge warnb">rate-limited mid-run</span>')
        for axis in ("accuracy", "fluency", "register"):
            v = s["quality"].get(axis)
            if v is not None:
                out.append(f'<span class="badge">{axis[:3]} {v}</span>')
        out.append("</div></div>")
    out.append("</div></section>")

    # ---- latency bars ----
    out.append('<section><h2>Latency per translation</h2><div class="bars">')
    for name in providers:
        s = summary[name]
        p50, p95 = s["latency_ms"]["p50"] or 0, s["latency_ms"]["p95"] or 0
        out.append(
            f'<div class="bar-row" style="--hue:var(--hue-{name})">'
            f'<div class="lab">{esc(name)}</div>'
            f'<div class="track"><div class="fill ghost" '
            f'style="width:{p95 / worst_p95 * 100:.1f}%"></div>'
            f'<div class="fill" style="width:{p50 / worst_p95 * 100:.1f}%"></div>'
            f'</div><div class="num">{p50} ms</div></div>'
        )
    out.append("</div>")
    out.append(
        '<div class="legend"><span><i class="swatch solid"></i>p50 (median)</span>'
        '<span><i class="swatch ghost"></i>p95</span></div>'
    )
    for name, flag in flags.items():
        if flag:
            out.append(
                f'<div class="alert"><b>{esc(name)} was rate-limited during '
                f'this run, so its latency here is not its speed.</b> It held '
                f'{flag["early"]} ms through the first half and then stepped '
                f'to {flag["late"]} ms and stayed there — the signature of a '
                f'quota kicking in, not of a slow model. Treat '
                f'{flag["early"]} ms as the honest figure and re-run with '
                f'request pacing to confirm.</div>'
            )
    out.append("</section>")

    # ---- phrase table ----
    out.append("<section><h2>Phrase by phrase</h2><div class=\"scroll\"><table>")
    out.append(f"<thead><tr><th>at</th><th>{esc(source_label)}</th>")
    for name in providers:
        out.append(
            f'<th class="hue" style="--hue:var(--hue-{name})">{esc(name)}</th>'
        )
    out.append("</tr></thead><tbody>")

    for row in rows:
        verdict = row.get("judgment")
        winner = verdict["rank"][0] if verdict and verdict.get("rank") else None
        secs = row.get("audio_ms", 0) / 1000
        out.append(
            f'<tr><td class="stamp">{int(secs // 60)}:{int(secs % 60):02d}</td>'
            f'<td class="en">{esc(row["en"])}'
        )
        if verdict and verdict.get("note"):
            out.append(f'<div class="note">{esc(verdict["note"])}</div>')
        out.append("</td>")
        for name in providers:
            t = row["translations"].get(name, {})
            cls = ("ko best" if name == winner else "ko") + target_cls
            out.append(f'<td class="{cls}">')
            if t.get("error"):
                out.append(f'<span class="err">{esc(t["error"])}</span>')
            else:
                out.append(esc(t.get("ko")))
            out.append(f'<div class="tick"><span>{num(t.get("latency_ms"))} ms</span>')
            if verdict and name in verdict["scores"]:
                sc = verdict["scores"][name]
                trio = "/".join(
                    str(sc.get(a) if sc.get(a) is not None else "-")
                    for a in ("accuracy", "fluency", "register")
                )
                out.append(f'<span class="sc">{trio}</span>')
            if name == winner:
                out.append('<span class="w">WIN</span>')
            out.append("</div></td>")
        out.append("</tr>")
    out.append("</tbody></table></div></section>")

    out.append(
        "<footer>Scores are <code>accuracy/fluency/register</code>, each 1–5, "
        "from one blind comparison per phrase. Latency is the translation call "
        "only — transcription is shared and excluded. Timestamps mark when the "
        "phrase finalized in the stream, not a word-level alignment."
        "</footer></div>"
    )
    return "".join(out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--results", default="benchmarks/run/results.json")
    p.add_argument("--out", default="benchmarks/run/dashboard.html")
    args = p.parse_args()
    data = json.loads(Path(args.results).read_text())
    Path(args.out).write_text(render(data))
    print(f"wrote {args.out}")
