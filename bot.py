"""
Corporate Takeover - AI Game Bot
=================================
Uses Claude (vision + adaptive thinking) to analyze the iOS Simulator screen
and automatically play Corporate Takeover by tapping the optimal moves.

Requirements:
  - macOS with Xcode installed
  - iOS Simulator running with Corporate Takeover open
  - ANTHROPIC_API_KEY environment variable set

Usage:
  python bot.py              # Auto-play mode
  python bot.py --coach      # Coach mode: explains every decision in detail
  python bot.py --once       # Take one action then exit (useful for testing)
"""

import argparse
import base64
import json
import subprocess
import sys
import time

import anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCREENSHOT_PATH = "/tmp/corporate_takeover.png"

# iPhone 14 Pro screen dimensions in points (default Simulator size).
# If using a different Simulator device, adjust these.
DEVICE_WIDTH = 393
DEVICE_HEIGHT = 852

# Seconds to wait after each tap before the next screenshot.
TAP_SETTLE_DELAY = 1.2

# If the bot sees no actionable state this many times in a row, it pauses.
MAX_CONSECUTIVE_WAITS = 15

# ---------------------------------------------------------------------------
# Game knowledge injected into every prompt
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
  - Each card has different skill attributes and power values.
  - GOAL: Pick 5 cards with DIVERSE, COMPLEMENTARY skills so you have
    coverage across many business strength categories.
  - Avoid picking 5 cards that all excel in the same category —
    balance is stronger than specialisation.
  - Prefer higher-rarity cards when skill coverage is equal.
  - If you can see the opponent's deck hint, counter their strengths.

COMBAT PHASE (after team selection):
  - Rounds are played one at a time.
  - Each round, a business strength category is contested.
  - The player whose team has higher combined power in that category wins.
  - Win enough rounds to win the match.

TEAM MANAGEMENT (mid-game):
  - After the initial selection, you CAN keep the same 5 cards for the
    whole game OR replace individual cards when the opportunity arises.
  - Replace a card only if the replacement is strictly better:
    higher rarity, higher power in a category you currently lack, or
    fixes an obvious weakness in the current team.
  - Do NOT replace a card just because a new one appears — stability
    and familiarity with your team's synergies has value.

RECRUITMENT:
  - Between matches you can recruit new executive cards.
  - Prefer cards that fill skill gaps in your existing collection.

BATTLE PHASE — HOW IT WORKS (like Pokémon type matchups):
  - Each battle round presents a CHALLENGE ATTRIBUTE (e.g. "Tech", "Finance",
    "Leadership", "Marketing", "Operations", "Innovation", "Strategy",
    "Negotiation", "HR", "Sales").
  - From your team of 5, you SELECT EXACTLY 3 CARDS to send into that battle.
  - The 3 cards you pick compete against the opponent's 3 cards.
  - Higher combined (or individual) power in the CHALLENGE ATTRIBUTE wins.
  - STRATEGY: always pick the 3 cards that have the highest value in the
    specific attribute being challenged. Ignore overall power — only the
    relevant attribute matters for this round.
  - If two cards tie on the challenge attribute, prefer the higher-rarity one
    as a tiebreaker (it likely has higher secondary stats).
  - You cannot use the same card in consecutive rounds (if that rule applies —
    adapt once you can see the UI).

=== STRATEGIC PRIORITIES ===
1. BATTLE SELECTION: Identify the challenge attribute, then rank your 5 cards
   by that attribute's value and send the top 3. This is the most important
   decision in the game.
2. TEAM SELECTION: Pick 5 cards that cover all 10 attribute categories so you
   are never completely outmatched on any challenge.
3. Prefer higher-rarity cards when attribute values are equal.
4. Save Legendary/Epic cards — if you can choose which to hold back, keep
   your best card in reserve for the hardest challenge.
5. In recruitment, target cards that fill your weakest attribute categories.
"""

# ---------------------------------------------------------------------------
# iOS Simulator helpers
# ---------------------------------------------------------------------------

def check_simulator() -> None:
    """Verify that at least one iOS Simulator is booted."""
    result = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "booted"],
        capture_output=True, text=True,
    )
    if "Booted" not in result.stdout:
        print("ERROR: No booted iOS Simulator found.")
        print("Open Xcode → Simulator, launch a device, then start Corporate Takeover.")
        sys.exit(1)


def take_screenshot() -> str:
    """Capture the Simulator screen and return the image as a base64 PNG string."""
    subprocess.run(
        ["xcrun", "simctl", "io", "booted", "screenshot", SCREENSHOT_PATH],
        check=True,
        capture_output=True,
    )
    with open(SCREENSHOT_PATH, "rb") as fh:
        return base64.standard_b64encode(fh.read()).decode("utf-8")


def tap(x: int, y: int) -> None:
    """Tap at point (x, y) on the booted Simulator."""
    subprocess.run(
        ["xcrun", "simctl", "io", "booted", "tap", str(x), str(y)],
        check=True,
    )
    time.sleep(TAP_SETTLE_DELAY)


def swipe(x1: int, y1: int, x2: int, y2: int, duration: float = 0.4) -> None:
    """Swipe from (x1,y1) to (x2,y2) over `duration` seconds."""
    subprocess.run(
        [
            "xcrun", "simctl", "io", "booted", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(duration),
        ],
        check=True,
    )
    time.sleep(TAP_SETTLE_DELAY)

# ---------------------------------------------------------------------------
# Claude-powered game analysis
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """\
{game_context}

The device screen is {width}×{height} points (width × height).
Top-left is (0, 0); bottom-right is ({width}, {height}).

Carefully examine the screenshot and respond with ONLY a JSON object — no markdown, \
no explanation outside the JSON.

JSON schema:
{{
  "screen": "<name of current screen, e.g. main_menu | team_selection | \
team_management | recruitment | battle | card_play | result | loading | \
waiting_for_opponent | unknown>",
  "game_state_summary": "<1–3 sentence description of what you see>",
  "challenge_attribute": "<the skill/attribute being contested this round, e.g. Tech | Finance | null if not in battle>",
  "my_cards": [
    {{
      "name": "<card name or description>",
      "power": <overall power number or null>,
      "rarity": "<Common|Uncommon|Rare|Epic|Legendary|unknown>",
      "top_skills": ["<skill1>", "<skill2>"],
      "challenge_attribute_value": <this card's value in the challenge_attribute, or null>,
      "selected": <true if already chosen/tapped, false if available>
    }}
  ],
  "battle_pick_note": "<only during battle: list which 3 cards you are picking and their challenge-attribute values, e.g. 'Picking Alice(Tech=92), Bob(Tech=85), Carol(Tech=78) — highest Tech on team'>",
  "team_selection_note": "<only for team_selection/team_management screens: \
explain which 5 you are picking and why they cover diverse skill categories>",
  "opponent_visible": "<description of opponent cards/power if visible, else null>",
  "reasoning": "<step-by-step strategic reasoning for the chosen action>",
  "action": {{
    "type": "<tap|swipe|wait>",
    "x": <integer, required for tap/swipe>,
    "y": <integer, required for tap/swipe>,
    "x2": <integer, only for swipe>,
    "y2": <integer, only for swipe>,
    "description": "<human-readable label for this action>"
  }}
}}

If no action is possible yet (loading, waiting for opponent, mid-animation), \
set "action" to {{"type": "wait", "description": "waiting"}}.
"""


def decide(client: anthropic.Anthropic, screenshot_b64: str, coach: bool) -> dict:
    """
    Send the screenshot to Claude and get back a structured action decision.
    With `coach=True`, adaptive thinking is enabled for richer explanations.
    """
    prompt = ANALYSIS_PROMPT.format(
        game_context=GAME_CONTEXT,
        width=DEVICE_WIDTH,
        height=DEVICE_HEIGHT,
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

    # Enable adaptive thinking in coach mode for deeper strategic reasoning.
    if coach:
        kwargs["thinking"] = {"type": "adaptive"}

    response = client.messages.create(**kwargs)

    # Extract the text block (thinking blocks, if present, are separate).
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
    if coach:
        print("Mode: COACH  (detailed explanations enabled)")
    else:
        print("Mode: AUTO-PLAY")
    print("Press Ctrl+C to stop.\n")

    check_simulator()
    api_client = anthropic.Anthropic()

    consecutive_waits = 0
    step = 0

    while True:
        step += 1
        print(f"[Step {step}] Taking screenshot…")

        try:
            screenshot_b64 = take_screenshot()
        except subprocess.CalledProcessError as exc:
            print(f"  Screenshot failed: {exc}")
            time.sleep(2)
            continue

        print(f"[Step {step}] Asking Claude…")
        try:
            result = decide(api_client, screenshot_b64, coach=coach)
        except json.JSONDecodeError as exc:
            print(f"  Could not parse Claude response: {exc}")
            time.sleep(1)
            continue
        except anthropic.APIError as exc:
            print(f"  API error: {exc}")
            time.sleep(5)
            continue

        # ---- Print state summary ----------------------------------------
        print(f"  Screen  : {result.get('screen', '?')}")
        print(f"  State   : {result.get('game_state_summary', '?')}")

        if coach:
            print(f"  Thinking: {result.get('reasoning', '')}")

        cards = result.get("my_cards", [])
        if cards:
            card_strs = [
                f"{c.get('name','?')} [{c.get('rarity','?')}]"
                + (f" pw:{c['power']}" if c.get("power") else "")
                + (" ✓" if c.get("selected") else "")
                for c in cards
            ]
            print(f"  My cards: {', '.join(card_strs)}")

        challenge = result.get("challenge_attribute")
        if challenge:
            print(f"  Challenge: {challenge}")

        battle_note = result.get("battle_pick_note")
        if battle_note:
            print(f"  Battle pick: {battle_note}")

        note = result.get("team_selection_note")
        if note:
            print(f"  Team pick: {note}")

        opp = result.get("opponent_visible")
        if opp:
            print(f"  Opponent: {opp}")

        # ---- Execute action ------------------------------------------------
        action = result.get("action", {})
        action_type = action.get("type", "wait")

        if action_type == "wait":
            consecutive_waits += 1
            print(f"  Action  : waiting… ({consecutive_waits}/{MAX_CONSECUTIVE_WAITS})")
            if consecutive_waits >= MAX_CONSECUTIVE_WAITS:
                print("\nBot has been waiting too long. Check the Simulator.")
                print("(Is the game still running? Is it your turn?)")
                break
            time.sleep(2)
        else:
            consecutive_waits = 0
            desc = action.get("description", action_type)

            if action_type == "tap":
                x, y = int(action["x"]), int(action["y"])
                print(f"  Action  : TAP  ({x}, {y})  — {desc}")
                tap(x, y)

            elif action_type == "swipe":
                x1, y1 = int(action["x"]), int(action["y"])
                x2, y2 = int(action["x2"]), int(action["y2"])
                print(f"  Action  : SWIPE ({x1},{y1}) → ({x2},{y2})  — {desc}")
                swipe(x1, y1, x2, y2)

            else:
                print(f"  Unknown action type '{action_type}', skipping.")

        print()

        if once:
            print("--once flag set, exiting after one action.")
            break

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI bot that auto-plays Corporate Takeover on iOS Simulator."
    )
    parser.add_argument(
        "--coach",
        action="store_true",
        help="Enable coach mode: print detailed strategic reasoning for each move.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Take exactly one action then exit (useful for testing).",
    )
    args = parser.parse_args()

    try:
        run(coach=args.coach, once=args.once)
    except KeyboardInterrupt:
        print("\nBot stopped by user.")


if __name__ == "__main__":
    main()
