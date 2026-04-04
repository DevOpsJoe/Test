"""
Corporate Takeover - AI Game Bot
=================================
Uses Claude vision to analyze the game window and automatically play
Corporate Takeover (TestFlight build running natively on Mac).

Requirements:
  - Apple Silicon Mac running the Corporate Takeover TestFlight build natively
  - ANTHROPIC_API_KEY environment variable set
  - pip install -r requirements.txt
  - Accessibility permission: System Settings → Privacy & Security →
    Accessibility → enable Terminal (or your Python runner)

Usage:
  python bot.py              # Auto-play mode
  python bot.py --coach      # Coach mode: explains every decision in detail
  python bot.py --once       # Take one action then exit (useful for testing)
"""

import argparse
import base64
import io
import json
import subprocess
import sys
import time

import anthropic
import pyautogui

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The macOS process name for the game. When an iOS/TestFlight app runs
# natively on Mac this is usually the app's display name.
APP_NAME = "Corporate Takeover"

# Seconds to wait after each click for the UI to settle.
TAP_SETTLE_DELAY = 1.2

# How many consecutive "no action" frames before the bot warns and pauses.
MAX_CONSECUTIVE_WAITS = 15

# ---------------------------------------------------------------------------
# Game knowledge injected into every Claude prompt
# ---------------------------------------------------------------------------

GAME_CONTEXT = """\
Corporate Takeover is a collectible card game (CCG) for iPhone set in the corporate world.

=== CORE MECHANICS ===

EXECUTIVE CARDS
  Each card represents a business executive with various skill attributes.
  Cards have up to 10 different "business strength" categories such as:
  Leadership, Finance, Marketing, Operations, Innovation, Strategy,
  Negotiation, Technology, HR, and Sales.

RARITY TIERS (weakest → strongest):
  Common → Uncommon → Rare → Epic → Legendary

TEAM SELECTION PHASE (happens at the start of each match):
  - You are shown a roster of available employee/executive cards.
  - You must SELECT EXACTLY 5 cards to form your battle team.
  - GOAL: Pick 5 cards with DIVERSE, COMPLEMENTARY skills so you have
    coverage across many business strength categories.
  - Avoid picking 5 cards that all excel in the same category —
    balance is stronger than specialisation.
  - Prefer higher-rarity cards when skill coverage is equal.

TEAM MANAGEMENT (mid-game):
  - After the initial selection you CAN keep the same 5 cards for the
    whole game OR replace individual cards when the opportunity arises.
  - Replace a card only if the replacement is strictly better:
    higher rarity, higher power in a category you currently lack, or
    fixes an obvious weakness.
  - Do NOT replace a card just because a new one appears — stability
    and familiarity with your team's synergies has value.

BATTLE PHASE — HOW IT WORKS (like Pokémon type matchups):
  - Each battle challenge names a CHALLENGE ATTRIBUTE (e.g. "Tech", "Finance",
    "Leadership", "Marketing", "Operations", "Innovation", "Strategy",
    "Negotiation", "HR", "Sales").
  - From your team of 5, you SELECT EXACTLY 3 CARDS to field for this challenge.
  - The battle is fought over 3 ROUNDS: each round is 1 of your cards vs 1
    opponent card (head-to-head). Higher value in the CHALLENGE ATTRIBUTE wins.
  - We ONLY play Virtual mode (3 rounds, easier) — never the 5-round hard mode.
  - Win at least 2 of 3 rounds to win the battle and earn XP + ACR tokens.

CARD SELECTION STRATEGY (which 3 to pick — always blind):
  - You NEVER see the opponent's cards before selecting. Pick purely on your own.
  - Always pick the 3 cards with the HIGHEST value in the CHALLENGE ATTRIBUTE.
  - Tiebreaker: prefer higher-rarity cards (Legendary > Epic > Rare > etc.).
  - After tapping your 3 cards, tap the confirm/start button to begin.

CARD ORDER STRATEGY (how to sequence across 3 rounds):
  - Play STRONGEST card first — take an early lead.
  - Play MEDIUM card second — secure or extend the lead.
  - Play WEAKEST card last — the match is usually decided by then.
  - Never hold a strong card in reserve; there are only 3 rounds.
  - A 3-0 sweep earns bonus XP — always lead with your best card.

COMPANY STORE — SPENDING ACR ON BOOSTS:
  - The Company Store sells boosts using ACR tokens.
  - Example: "Coffee Run" costs 65 ACR → +10% team power.
  - Prefer team-wide boosts. Buy BEFORE entering a battle.
  - Keep a ~100 ACR reserve. Don't spend all ACR if a high-rarity
    card is within reach in recruitment — permanent > temporary.
  - Cheap boosts (≤50 ACR) that improve a commonly-challenged stat are
    almost always worth buying.

REWARDS & PROGRESSION — THE META-GOAL:
  - THE PRIMARY GOAL IS TO ACCUMULATE XP (Experience Points).
  - XP advances your rank on the CORPORATE LADDER
    (Intern → Associate → Manager → Director → VP → C-Suite …).
  - Every battle earns XP — wins earn significantly more than losses.
  - ACR tokens are spent in the Company Store (boosts) or on recruitment.
  - NEVER idle — every button press is a step toward the next promotion.

=== STRATEGIC PRIORITIES ===
1. MAXIMISE XP — win battles, complete challenges, recruit cards.
2. CARD ORDER: Lead strongest → medium → weakest every battle.
3. CARD SELECTION: Pick the 3 highest-attribute cards for the challenge.
4. TEAM SELECTION: Cover all 10 attribute categories across your 5 picks.
5. SPEND ACR WISELY between boosts (Company Store) and recruitment.
6. NEVER idle — tap through every reward screen immediately.

NAVIGATION — EXACT BUTTON SEQUENCE:
  main screen
    → tap "Work Remotely"
    → tap "Virtual"  (3-round easy mode — never the 5-round hard mode)
    → tap "Join Virtual Meeting"  (button at the BOTTOM of the screen)
    → team selection  (pick 5 cards)
    → battle  (3 rounds of 1v1)
    → collect XP + ACR reward
    → tap "Back to Office"  (returns to main screen)
    → repeat indefinitely.
"""

# ---------------------------------------------------------------------------
# Game window helpers (native Mac / TestFlight)
# ---------------------------------------------------------------------------

def _get_window_bounds() -> tuple[int, int, int, int]:
    """
    Return (x, y, width, height) of the game window using AppleScript.
    Works for any native Mac app, including iOS apps running via TestFlight.
    """
    script = f"""
    tell application "System Events"
        tell process "{APP_NAME}"
            set w to front window
            set {{wx, wy}} to position of w
            set {{ww, wh}} to size of w
            return (wx as string) & "," & (wy as string) & "," & \\
                   (ww as string) & "," & (wh as string)
        end tell
    end tell
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, check=True,
    )
    parts = [p.strip() for p in result.stdout.strip().split(",")]
    return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])


def check_app() -> tuple[int, int, int, int]:
    """Verify the game is running and return its window bounds."""
    try:
        bounds = _get_window_bounds()
    except subprocess.CalledProcessError:
        print(f'ERROR: "{APP_NAME}" window not found.')
        print("Make sure Corporate Takeover is open and visible on screen.")
        sys.exit(1)
    return bounds


def take_screenshot(bounds: tuple[int, int, int, int]) -> tuple[str, int, int]:
    """
    Capture the game window. Returns (base64_png, window_width, window_height).
    Claude will receive the exact pixel dimensions so no coordinate mapping needed.
    """
    wx, wy, ww, wh = bounds
    img = pyautogui.screenshot(region=(wx, wy, ww, wh))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8"), ww, wh


def tap(x: int, y: int, bounds: tuple[int, int, int, int]) -> None:
    """Click at window-relative (x, y) — no coordinate mapping needed."""
    wx, wy, _ww, _wh = bounds
    pyautogui.click(wx + x, wy + y)
    time.sleep(TAP_SETTLE_DELAY)


def swipe(
    x1: int, y1: int, x2: int, y2: int,
    bounds: tuple[int, int, int, int],
    duration: float = 0.4,
) -> None:
    """Swipe inside the game window using window-relative coordinates."""
    wx, wy, _ww, _wh = bounds
    pyautogui.moveTo(wx + x1, wy + y1)
    pyautogui.dragTo(wx + x2, wy + y2, duration=duration, button="left")
    time.sleep(TAP_SETTLE_DELAY)

# ---------------------------------------------------------------------------
# Claude-powered game analysis
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """\
{game_context}

The game window in this screenshot is {width}×{height} pixels (width × height).
Top-left is (0, 0); bottom-right is ({width}, {height}).
All coordinates you return must be relative to this window — (0,0) is the
top-left corner of the game window as shown in the screenshot.

Carefully examine the screenshot and respond with ONLY a JSON object — \
no markdown, no explanation outside the JSON.

JSON schema:
{{
  "screen": "<main_menu | team_selection | team_management | company_store | \
recruitment | battle | card_play | post_battle | xp_reward | corporate_ladder | \
daily_challenge | loading | unknown>",
  "game_state_summary": "<1–3 sentence description of what you see>",
  "challenge_attribute": "<challenge skill name, e.g. Tech | Finance | null>",
  "my_cards": [
    {{
      "name": "<card name>",
      "power": <number or null>,
      "rarity": "<Common|Uncommon|Rare|Epic|Legendary|unknown>",
      "top_skills": ["<skill1>", "<skill2>"],
      "challenge_attribute_value": <value in challenge attr, or null>,
      "selected": <true if already tapped/chosen, false if available>
    }}
  ],
  "xp_visible": "<e.g. '1240/2000 XP' or null>",
  "ladder_rank": "<e.g. 'Manager' or null>",
  "acr_tokens": "<ACR count if visible or null>",
  "battle_round": <1, 2, or 3, or null>,
  "battle_score": "<e.g. 'You 1 - Opponent 0' or null>",
  "battle_pick_note": "<card selection reasoning with attribute values>",
  "battle_order_note": "<which card to play this round and why>",
  "team_selection_note": "<which 5 cards and why they cover diverse skills>",
  "store_items": [
    {{
      "name": "<boost name>",
      "cost_acr": <integer>,
      "effect": "<what it does>",
      "buy_recommendation": "<buy_now|save_acr|skip>",
      "buy_reason": "<one sentence>"
    }}
  ],
  "reasoning": "<step-by-step reasoning for the chosen action>",
  "action": {{
    "type": "<tap|swipe|wait>",
    "x": <integer iPhone-space x, required for tap/swipe>,
    "y": <integer iPhone-space y, required for tap/swipe>,
    "x2": <integer, swipe endpoint x only>,
    "y2": <integer, swipe endpoint y only>,
    "description": "<what you are doing>"
  }}
}}

If no action is possible (loading, animation, waiting), set:
  "action": {{"type": "wait", "description": "waiting"}}
"""


def decide(
    client: anthropic.Anthropic,
    screenshot_b64: str,
    win_w: int,
    win_h: int,
    coach: bool,
) -> dict:
    """Ask Claude to analyse the screenshot and return a structured action."""
    prompt = ANALYSIS_PROMPT.format(
        game_context=GAME_CONTEXT,
        width=win_w,
        height=win_h,
    )

    kwargs: dict = dict(
        model="claude-opus-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    # Adaptive thinking gives richer strategic explanations in coach mode.
    if coach:
        kwargs["thinking"] = {"type": "adaptive"}

    response = client.messages.create(**kwargs)
    text = next(b.text for b in response.content if b.type == "text")

    # Strip accidental markdown fences.
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return json.loads(text.strip())

# ---------------------------------------------------------------------------
# Main bot loop
# ---------------------------------------------------------------------------

def run(coach: bool = False, once: bool = False) -> None:
    print("Corporate Takeover Bot")
    print("=" * 40)
    print(f"Mode: {'COACH (detailed reasoning)' if coach else 'AUTO-PLAY'}")
    print("Press Ctrl+C to stop.\n")

    bounds = check_app()
    print(f"Game window found at {bounds}\n")

    api_client = anthropic.Anthropic()
    consecutive_waits = 0
    step = 0

    while True:
        step += 1
        # Refresh window position each step in case the window was moved.
        try:
            bounds = _get_mirror_window_bounds()
        except subprocess.CalledProcessError:
            print("  Mirror window lost — waiting…")
            time.sleep(3)
            continue

        print(f"[Step {step}] Taking screenshot…")
        screenshot_b64, win_w, win_h = take_screenshot(bounds)

        print(f"[Step {step}] Asking Claude…")
        try:
            result = decide(api_client, screenshot_b64, win_w, win_h, coach=coach)
        except json.JSONDecodeError as exc:
            print(f"  Could not parse response: {exc}")
            time.sleep(1)
            continue
        except anthropic.APIError as exc:
            print(f"  API error: {exc}")
            time.sleep(5)
            continue

        # ---- Print status --------------------------------------------------
        print(f"  Screen  : {result.get('screen', '?')}")
        print(f"  State   : {result.get('game_state_summary', '?')}")

        parts = []
        if result.get("ladder_rank"):
            parts.append(f"Rank: {result['ladder_rank']}")
        if result.get("xp_visible"):
            parts.append(f"XP: {result['xp_visible']}")
        if result.get("acr_tokens"):
            parts.append(f"ACR: {result['acr_tokens']}")
        if parts:
            print(f"  Progress: {' | '.join(parts)}")

        if coach:
            print(f"  Thinking: {result.get('reasoning', '')}")

        cards = result.get("my_cards") or []
        if cards:
            card_strs = [
                f"{c.get('name','?')} [{c.get('rarity','?')}]"
                + (f" pw:{c['power']}" if c.get("power") else "")
                + (" ✓" if c.get("selected") else "")
                for c in cards
            ]
            print(f"  Cards   : {', '.join(card_strs)}")

        if result.get("challenge_attribute"):
            suffix = ""
            if result.get("battle_round"):
                suffix += f"  Round {result['battle_round']}/3"
            if result.get("battle_score"):
                suffix += f"  Score: {result['battle_score']}"
            print(f"  Challenge: {result['challenge_attribute']}{suffix}")

        if result.get("battle_pick_note"):
            print(f"  Pick    : {result['battle_pick_note']}")
        if result.get("battle_order_note"):
            print(f"  Order   : {result['battle_order_note']}")
        if result.get("team_selection_note"):
            print(f"  Team    : {result['team_selection_note']}")

        for item in result.get("store_items") or []:
            sym = {"buy_now": "✓", "save_acr": "~", "skip": "✗"}.get(
                item.get("buy_recommendation", ""), "?"
            )
            print(
                f"  Store [{sym}] {item.get('name')}  "
                f"{item.get('cost_acr')} ACR — {item.get('effect')}  "
                f"({item.get('buy_reason')})"
            )

        # ---- Execute action ------------------------------------------------
        action = result.get("action") or {}
        action_type = action.get("type", "wait")

        if action_type == "wait":
            consecutive_waits += 1
            print(
                f"  Action  : waiting… "
                f"({consecutive_waits}/{MAX_CONSECUTIVE_WAITS})"
            )
            if consecutive_waits >= MAX_CONSECUTIVE_WAITS:
                print("\nBot waiting too long — check the game window is visible.")
                break
            time.sleep(2)
        else:
            consecutive_waits = 0
            desc = action.get("description", action_type)

            if action_type == "tap":
                x, y = int(action["x"]), int(action["y"])
                print(f"  Action  : TAP ({x}, {y}) — {desc}")
                tap(x, y, bounds)

            elif action_type == "swipe":
                x1, y1 = int(action["x"]), int(action["y"])
                x2, y2 = int(action["x2"]), int(action["y2"])
                print(f"  Action  : SWIPE ({x1},{y1})→({x2},{y2}) — {desc}")
                swipe(x1, y1, x2, y2, bounds)

            else:
                print(f"  Unknown action type '{action_type}', skipping.")

        print()

        if once:
            print("--once flag set, exiting.")
            break

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI bot for Corporate Takeover (TestFlight native Mac build)."
    )
    parser.add_argument(
        "--coach",
        action="store_true",
        help="Print detailed strategic reasoning for every move.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Take exactly one action then exit (good for testing).",
    )
    args = parser.parse_args()

    try:
        run(coach=args.coach, once=args.once)
    except KeyboardInterrupt:
        print("\nBot stopped.")


if __name__ == "__main__":
    main()
