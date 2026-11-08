#!/usr/bin/env python3
"""Generate the 8-slide ProofLoop Blast Radius Zero deck with python-pptx."""
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

OUT = Path(__file__).resolve().parent / "ProofLoop-BlastRadiusZero.pptx"

NAVY = "1E2761"
PANEL = "27347A"
ICE = "CADCFC"
WHITE = "FFFFFF"
INK = "1F2328"
MUTED = "57606A"
LIGHT = "F6F8FA"
LINE = "D0D7DE"
GREEN = "1A7F37"
RED = "CF222E"
MINT = "02C39A"
SOFT = "9DB2E8"
CODE_BG = "0D1117"
CODE_FG = "E6EDF3"

HEAD = "Cambria"
BODY = "Calibri"
MONO = "Courier New"


def rgb(hex_value):
    return RGBColor.from_string(hex_value)


def bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = rgb(color)


def tb(slide, x, y, w, h, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = Inches(0.02)
    frame.margin_right = Inches(0.02)
    frame.margin_top = Inches(0.02)
    frame.margin_bottom = Inches(0.02)
    frame.vertical_anchor = anchor
    return frame


def para(frame, text, size=16, bold=False, color=INK, font=BODY, italic=False,
         align=PP_ALIGN.LEFT, before=0, after=0, first=False):
    p = frame.paragraphs[0] if first else frame.add_paragraph()
    p.alignment = align
    p.space_before = Pt(before)
    p.space_after = Pt(after)
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.name = font
    run.font.color.rgb = rgb(color)
    return p


def card(slide, x, y, w, h, fill, outline=None, radius=0.08):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
    )
    shape.adjustments[0] = radius
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(fill)
    if outline:
        shape.line.color.rgb = rgb(outline)
        shape.line.width = Pt(1)
    else:
        shape.line.fill.background()
    shape.shadow.inherit = False
    frame = shape.text_frame
    frame.word_wrap = True
    frame.margin_left = Inches(0.16)
    frame.margin_right = Inches(0.16)
    frame.margin_top = Inches(0.12)
    frame.margin_bottom = Inches(0.12)
    return shape, frame


def circle(slide, x, y, d, fill, label, color=WHITE, size=15):
    shape = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(fill)
    shape.line.fill.background()
    shape.shadow.inherit = False
    frame = shape.text_frame
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = label
    run.font.size = Pt(size)
    run.font.bold = True
    run.font.name = BODY
    run.font.color.rgb = rgb(color)


def arrow(slide, x, y, w, h, color):
    shape = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(color)
    shape.line.fill.background()
    shape.shadow.inherit = False


def footer(slide, number, dark=False):
    color = SOFT if dark else "8B949E"
    frame = tb(slide, 0.8, 7.08, 6.0, 0.3)
    para(frame, "ProofLoop Blast Radius Zero", 9, False, color, first=True)
    frame = tb(slide, 11.4, 7.08, 1.1, 0.3)
    para(frame, str(number), 9, False, color, align=PP_ALIGN.RIGHT, first=True)


def title(slide, text, dark=False, size=34):
    frame = tb(slide, 0.8, 0.5, 11.8, 0.9)
    para(frame, text, size, True, ICE if dark else NAVY, HEAD, first=True)


def subtitle(slide, text, dark=False):
    frame = tb(slide, 0.8, 1.28, 11.4, 0.5)
    para(frame, text, 16, False, SOFT if dark else MUTED, first=True)


def build():
    pres = Presentation()
    pres.slide_width = Inches(13.333)
    pres.slide_height = Inches(7.5)
    blank = pres.slide_layouts[6]

    # ---------------------------------------------------------------- slide 1
    slide = pres.slides.add_slide(blank)
    bg(slide, NAVY)
    frame = tb(slide, 0.9, 1.35, 8.2, 2.4)
    para(frame, "ProofLoop", 54, True, WHITE, HEAD, first=True)
    para(frame, "Blast Radius Zero", 44, True, MINT, HEAD, after=6)
    frame = tb(slide, 0.9, 3.95, 7.6, 1.2)
    para(frame, "Verifiable acceptance for coding agents that run untrusted code.",
         19, False, ICE, after=6, first=True)
    para(frame, "Vultr Agent Rush Hackathon  ·  Blast Radius Zero  ·  2026-11-08",
         13, False, SOFT)
    box, frame = card(slide, 9.15, 1.4, 3.3, 3.4, PANEL, ICE, radius=0.06)
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    para(frame, "ISOLATED SANDBOX", 12, True, MINT, first=True, after=10)
    para(frame, "Docker container", 17, True, WHITE, after=8)
    for line in ("--network none", "512 MB  ·  1 CPU", "cap-drop ALL", "pids-limit 128"):
        para(frame, line, 13, False, ICE, MONO, after=5)
    frame = tb(slide, 9.15, 5.05, 3.3, 1.0)
    para(frame, "The agent executes here.\nThe verdict is made elsewhere.",
         13, False, SOFT, italic=True, first=True)
    frame = tb(slide, 0.9, 6.55, 7.0, 0.4)
    para(frame, "Apache-2.0  ·  derived from ProofLoop", 11, False, SOFT, first=True)

    # ---------------------------------------------------------------- slide 2
    slide = pres.slides.add_slide(blank)
    bg(slide, WHITE)
    title(slide, "A green test run is not proof of a fix")
    subtitle(slide, "An agent can pass the suite without solving the task.")
    cheats = [
        ("01", "Edit the tests", "Delete or weaken the failing assertions."),
        ("02", "Hardcode values", "Special-case the exact inputs the tests use."),
        ("03", "Plant test hooks", "Drop conftest.py, sitecustomize.py or .pth files mid-run."),
        ("04", "Forge the report", "Rewrite JUnit or coverage output from an atexit hook."),
    ]
    for index, (number, heading, body) in enumerate(cheats):
        x = 0.8 + index * 2.95
        box, frame = card(slide, x, 2.15, 2.7, 2.55, LIGHT)
        circle(slide, x + 0.2, 2.35, 0.5, NAVY, number, WHITE, 12)
        frame = tb(slide, x + 0.2, 3.0, 2.3, 0.5)
        para(frame, heading, 16, True, NAVY, first=True)
        frame = tb(slide, x + 0.2, 3.45, 2.3, 0.9)
        para(frame, body, 12.5, False, MUTED, first=True)
    box, frame = card(slide, 0.8, 5.05, 11.7, 1.35, NAVY, radius=0.05)
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    para(frame, "The agent's self-report is a claim, not evidence.", 22, True, ICE,
         HEAD, align=PP_ALIGN.CENTER, first=True)
    frame = tb(slide, 0.8, 6.55, 11.7, 0.4)
    para(frame, "ProofLoop treats every candidate patch as untrusted input.",
         15, True, NAVY, first=True)
    footer(slide, 2)

    # ---------------------------------------------------------------- slide 3
    slide = pres.slides.add_slide(blank)
    bg(slide, WHITE)
    title(slide, "Separate the executor from the accepting authority")
    subtitle(slide, "The model proposes. The verifier disposes.")
    columns = [
        ("1", "Untrusted executor", MINT,
         ["Vultr Serverless Inference", "ReAct loop with three tools", "Runs inside Docker"]),
        ("2", "Frozen acceptance", NAVY,
         ["Visible + hidden tests", "14 deterministic checks", "No LLM in the verdict path"]),
        ("3", "Replayable evidence", GREEN,
         ["evidence.json + SHA-256", "HTML report", "verify-evidence / replay"]),
    ]
    for index, (number, heading, accent, lines) in enumerate(columns):
        x = 0.8 + index * 4.0
        box, frame = card(slide, x, 1.95, 3.7, 3.55, LIGHT)
        circle(slide, x + 0.25, 2.2, 0.65, accent, number, WHITE, 18)
        frame = tb(slide, x + 0.25, 3.0, 3.2, 0.55)
        para(frame, heading, 21, True, NAVY, first=True)
        frame = tb(slide, x + 0.25, 3.65, 3.2, 1.6)
        for line in lines:
            para(frame, line, 13.5, False, MUTED, after=7, first=(line == lines[0]))
    box, frame = card(slide, 0.8, 5.85, 11.7, 0.9, NAVY, radius=0.08)
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    para(frame, "The LLM proposes. The verifier disposes.", 20, True, ICE, HEAD,
         align=PP_ALIGN.CENTER, first=True)
    footer(slide, 3)

    # ---------------------------------------------------------------- slide 4
    slide = pres.slides.add_slide(blank)
    bg(slide, NAVY)
    title(slide, "Architecture", dark=True)
    subtitle(slide, "One agent. One sandbox. One independent verdict.", dark=True)
    stages = [
        ("Vultr", "Serverless\nInference", "OpenAI-compatible chat + tools"),
        ("ReAct", "agent loop", "read_file · write_file · run_tests"),
        ("Docker", "sandbox", "no network · 512 MB · 1 CPU"),
        ("ProofLoop", "verifier", "14 checks on the candidate patch"),
        ("Evidence", "sheet", "JSON + SHA-256 + HTML report"),
    ]
    box_w, box_h, gap = 2.15, 1.55, 0.3
    start = 0.7
    for index, (heading, sub, caption) in enumerate(stages):
        x = start + index * (box_w + gap)
        fill = PANEL if index < 4 else "1B4D3E"
        outline = ICE if index < 4 else MINT
        box, frame = card(slide, x, 1.95, box_w, box_h, fill, outline, radius=0.07)
        frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        para(frame, heading, 16, True, WHITE, align=PP_ALIGN.CENTER, first=True)
        para(frame, sub, 12.5, False, ICE, align=PP_ALIGN.CENTER, after=2)
        frame = tb(slide, x - 0.1, 3.6, box_w + 0.2, 0.75)
        para(frame, caption, 11, False, SOFT, align=PP_ALIGN.CENTER, first=True)
        if index < len(stages) - 1:
            arrow(slide, x + box_w + 0.03, 2.55, gap - 0.06, 0.34, MINT)
    box, frame = card(slide, 0.7, 4.35, 11.95, 2.25, "202B63", radius=0.05)
    frame.vertical_anchor = MSO_ANCHOR.TOP
    para(frame, "THE VERDICT PATH  —  deterministic, no model", 11, True, MINT,
         first=True, after=6)
    checks_left = [
        "baseline_reproduced", "candidate_tests_passed", "visible_tests_match_baseline",
        "hidden_tests_passed", "hidden_tests_expected", "candidate_tree_frozen",
        "test_integrity",
    ]
    checks_right = [
        "scope", "noop", "hardcode_suspicion", "runtime_tamper_suspicion",
        "runtime_file_injection", "mutation_score", "coverage",
    ]
    frame = tb(slide, 0.9, 4.85, 5.7, 1.5)
    for name in checks_left:
        para(frame, "·  " + name, 10.5, False, ICE, MONO, after=1.5, first=(name == checks_left[0]))
    frame = tb(slide, 6.8, 4.85, 5.7, 1.5)
    for name in checks_right:
        para(frame, "·  " + name, 10.5, False, ICE, MONO, after=1.5, first=(name == checks_right[0]))
    frame = tb(slide, 0.7, 6.72, 11.9, 0.4)
    para(frame, "No network in the sandbox. No model in the verdict path.",
         15, True, ICE, first=True)
    footer(slide, 4, dark=True)

    # ---------------------------------------------------------------- slide 5
    slide = pres.slides.add_slide(blank)
    bg(slide, WHITE)
    title(slide, "Blast radius stays inside the box")
    subtitle(slide, "Defence in depth around untrusted code.")
    defences = [
        ("N", "No network", "The container cannot exfiltrate data or install packages.", MINT),
        ("R", "Resource caps", "512 MB, 1 CPU, 128 PIDs, all capabilities dropped.", NAVY),
        ("S", "Shadow-proof tools", "python -P -c launcher prevents repo-root shadowing.", "6D2E46"),
        ("H", "Signed results", "HMAC attestation is cross-checked against JUnit.", GREEN),
        ("Q", "Runtime quarantine", "Files planted during tests are removed and reported.", "9A6700"),
        ("W", "Write guards", "Only allowed_paths are writable; tests are denied.", RED),
    ]
    for index, (letter, heading, body, accent) in enumerate(defences):
        row, col = divmod(index, 3)
        x = 0.72 + col * 4.12
        y = 1.9 + row * 2.25
        box, frame = card(slide, x, y, 3.8, 1.95, LIGHT)
        circle(slide, x + 0.22, y + 0.25, 0.5, accent, letter, WHITE, 15)
        frame = tb(slide, x + 0.85, y + 0.22, 2.75, 0.5)
        para(frame, heading, 17, True, NAVY, first=True)
        frame = tb(slide, x + 0.22, y + 0.95, 3.35, 0.85)
        para(frame, body, 12.5, False, MUTED, first=True)
    frame = tb(slide, 0.8, 6.55, 11.7, 0.4)
    para(frame, "The sandbox bounds the blast radius; the verifier bounds the claim.",
         15, True, NAVY, first=True)
    footer(slide, 5)

    # ---------------------------------------------------------------- slide 6
    slide = pres.slides.add_slide(blank)
    bg(slide, NAVY)
    title(slide, "Two scenes, two verdicts", dark=True)
    subtitle(slide, "Same verifier. Only the candidate patch changes.", dark=True)
    panels = [
        (0.8, "SCENE 1  ·  AGENT FIX", "PASS", GREEN,
         ["14 / 14 checks", "hidden 3 / 3", "mutation 8 / 8 killed", "coverage 100%"]),
        (6.95, "SCENE 2  ·  CHEAT PATCH", "FAIL", RED,
         ["hidden_tests_passed", "hidden_tests_expected", "hardcode_suspicion", "verdict: FAIL"]),
    ]
    for x, label, verdict, accent, lines in panels:
        box, frame = card(slide, x, 1.95, 5.6, 4.35, PANEL, accent, radius=0.05)
        frame = tb(slide, x + 0.3, 2.15, 5.0, 0.4)
        para(frame, label, 11, True, SOFT, first=True)
        frame = tb(slide, x + 0.3, 2.55, 5.0, 0.95)
        para(frame, verdict, 42, True, accent, HEAD, first=True)
        frame = tb(slide, x + 0.3, 3.55, 5.0, 2.4)
        for line in lines:
            para(frame, line, 15, False, ICE, after=9, first=(line == lines[0]))
    frame = tb(slide, 0.8, 6.5, 11.8, 0.5)
    para(frame, "Keyless stack self-test with the bundled fix and cheat patches. "
                "The Vultr agent scene follows the same verification path.",
         13, False, SOFT, first=True)
    footer(slide, 6, dark=True)

    # ---------------------------------------------------------------- slide 7
    slide = pres.slides.add_slide(blank)
    bg(slide, WHITE)
    title(slide, "A web console with a containment view")
    subtitle(slide, "Run the agent, watch the box, open the evidence.")
    box, frame = card(slide, 0.8, 1.8, 5.6, 4.55, LIGHT)
    frame.vertical_anchor = MSO_ANCHOR.TOP
    para(frame, "RUN MODES", 10.5, True, GREEN, first=True, after=4)
    para(frame, "Vultr agent — real LLM, server-side key", 12.5, False, INK, after=2)
    para(frame, "Bundled fix — keyless self-test", 12.5, False, INK, after=12)
    para(frame, "CONTAINMENT PROBE", 10.5, True, GREEN, after=4)
    para(frame, "Network: blocked", 12.5, False, INK, after=2)
    para(frame, "Secret-looking env: none visible", 12.5, False, INK, after=2)
    para(frame, "512 MB · 1 CPU · cap-drop ALL", 12.5, False, INK, after=12)
    para(frame, "EVIDENCE", 10.5, True, GREEN, after=4)
    para(frame, "14 deterministic checks", 12.5, False, INK, after=2)
    para(frame, "PASS / FAIL + report links", 12.5, False, INK)
    shot = Path(__file__).resolve().parent.parent / "docs" / "web-demo-hero.png"
    if shot.is_file():
        slide.shapes.add_picture(str(shot), Inches(6.7), Inches(1.8), width=Inches(5.95))
    frame = tb(slide, 6.7, 6.5, 5.95, 0.4)
    para(frame, "UI preview from the keyless self-test; Vultr mode follows the same flow.",
         10, False, MUTED, first=True)
    footer(slide, 7)

    # ---------------------------------------------------------------- slide 8
    slide = pres.slides.add_slide(blank)
    bg(slide, NAVY)
    title(slide, "Deployed on Vultr", dark=True)
    subtitle(slide, "One VM is the control layer; the sandbox is the blast radius.", dark=True)
    box, frame = card(slide, 0.7, 1.9, 7.4, 4.5, PANEL, ICE, radius=0.05)
    frame.vertical_anchor = MSO_ANCHOR.TOP
    para(frame, "VULTR CLOUD COMPUTE · UBUNTU 24.04", 11, True, MINT, first=True, after=8)
    inner = [
        ("systemd", "uvicorn webapp:app"),
        ("Caddy", "public TLS reverse proxy"),
        ("Docker", "proofloop-sandbox:latest"),
        ("workspace", "repo · venv · runs"),
    ]
    for index, (heading, body) in enumerate(inner):
        row, col = divmod(index, 2)
        x = 1.0 + col * 3.45
        y = 2.55 + row * 1.35
        box2, frame2 = card(slide, x, y, 3.2, 1.15, "202B63", radius=0.07)
        frame2.vertical_anchor = MSO_ANCHOR.MIDDLE
        para(frame2, heading, 15, True, WHITE, first=True)
        para(frame2, body, 11, False, ICE)
    box3, frame3 = card(slide, 8.6, 2.1, 3.9, 1.55, "1B4D3E", MINT, radius=0.06)
    frame3.vertical_anchor = MSO_ANCHOR.MIDDLE
    para(frame3, "Vultr Serverless Inference", 15, True, WHITE, first=True)
    para(frame3, "api.vultrinference.com/v1", 11, False, ICE)
    para(frame3, "OpenAI-compatible · tools", 11, False, ICE)
    arrow(slide, 8.15, 2.6, 0.35, 0.3, MINT)
    box4, frame4 = card(slide, 8.6, 4.35, 3.9, 1.15, PANEL, ICE, radius=0.06)
    frame4.vertical_anchor = MSO_ANCHOR.MIDDLE
    para(frame4, "Public URL", 15, True, WHITE, first=True)
    para(frame4, "Caddy TLS · one run at a time", 11, False, ICE)
    frame = tb(slide, 0.7, 6.55, 11.9, 0.4)
    para(frame, "The key lives in /etc/proofloop/proofloop.env — never in the sandbox.",
         14, True, ICE, first=True)
    footer(slide, 8, dark=True)

    # ---------------------------------------------------------------- slide 9
    slide = pres.slides.add_slide(blank)
    bg(slide, WHITE)
    title(slide, "Evidence you can replay")
    subtitle(slide, "Hash the sheet, replay the run, compare the verdict.")
    box, frame = card(slide, 0.8, 1.9, 5.3, 4.55, LIGHT)
    frame = tb(slide, 1.0, 2.1, 4.9, 0.4)
    para(frame, "evidence.json", 15, True, NAVY, MONO, first=True)
    fields = [
        ("verdict", "PASS · FAIL · INSUFFICIENT_EVIDENCE"),
        ("patch_sha256", "hash of the candidate patch"),
        ("diff_sha256", "hash of the frozen diff"),
        ("acceptance_sha256", "hash of the acceptance criteria"),
        ("checks", "14 pass/fail entries with details"),
        ("environment", "python, sandbox, image id, versions"),
    ]
    frame = tb(slide, 1.0, 2.6, 4.9, 3.6)
    for label, body in fields:
        para(frame, label, 12, True, NAVY, MONO, after=1, first=(label == fields[0][0]))
        para(frame, body, 11.5, False, MUTED, after=9)
    box, frame = card(slide, 6.5, 1.9, 6.0, 4.55, CODE_BG, radius=0.04)
    frame = tb(slide, 6.7, 2.1, 5.6, 4.1)
    code = [
        ("$ python -m proofloop verify-evidence \\", CODE_FG, True),
        ("    --evidence runs/demo/evidence-pass/evidence.json", CODE_FG, False),
        ("evidence hash OK: 6fe27cf7...", MINT, False),
        ("", CODE_FG, False),
        ("$ python -m proofloop replay \\", CODE_FG, True),
        ("    --evidence runs/demo/evidence-pass/evidence.json \\", CODE_FG, False),
        ("    --repo examples/discount_demo --task task.json \\", CODE_FG, False),
        ("    --patch scene1-agent.patch --out runs/replay", CODE_FG, False),
        ("old verdict: PASS", ICE, False),
        ("new verdict: PASS", ICE, False),
        ("consistent: True", MINT, False),
    ]
    for line_text, color, bold in code:
        para(frame, line_text or " ", 11.5, bold, color, MONO, after=3,
             first=(line_text == code[0][0]))
    frame = tb(slide, 0.8, 6.55, 11.8, 0.4)
    para(frame, "A human reviews the evidence, not the model's claim.", 15, True, NAVY, first=True)
    footer(slide, 9)

    # ---------------------------------------------------------------- slide 8
    slide = pres.slides.add_slide(blank)
    bg(slide, NAVY)
    title(slide, "What this is — and what it is not", dark=True)
    columns = [
        (0.8, "HONEST LIMITS", SOFT, [
            "One synthetic task — not a benchmark.",
            "Some checks are heuristics, not proofs.",
            "Hidden tests are local to this demo.",
            "Model behaviour is not a general accuracy claim.",
        ]),
        (6.9, "NEXT", MINT, [
            "Keep hidden tests in a separate trust domain.",
            "Add gVisor / E2B sandbox backends.",
            "Evaluate across the Vultr model catalog.",
            "Wire verify-evidence into CI sign-off.",
        ]),
    ]
    for x, heading, accent, lines in columns:
        box, frame = card(slide, x, 1.9, 5.6, 3.9, PANEL, radius=0.05)
        frame = tb(slide, x + 0.3, 2.1, 5.0, 0.4)
        para(frame, heading, 12, True, accent, first=True)
        frame = tb(slide, x + 0.3, 2.6, 5.0, 3.0)
        for line in lines:
            para(frame, "·  " + line, 14, False, ICE, after=10, first=(line == lines[0]))
    frame = tb(slide, 0.8, 6.05, 11.8, 0.7)
    para(frame, "Small, real, reproducible.", 26, True, ICE, HEAD, first=True)
    frame = tb(slide, 0.8, 6.62, 11.8, 0.4)
    para(frame, "Trust the evidence, not the agent.", 13, False, SOFT, first=True)
    footer(slide, 10, dark=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pres.save(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(pres.slides)} slides)")


if __name__ == "__main__":
    build()
