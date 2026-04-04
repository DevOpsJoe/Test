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
  - Each battle challenge names a CHALLENGE ATTRIBUTE (e.g. "Tech", "Finance",
    "Leadership", "Marketing", "Operations", "Innovation", "Strategy",
    "Negotiation", "HR", "Sales").
  - From your team of 5, you SELECT EXACTLY 3 CARDS to field for this challenge.
  - The battle is then fought over 3 ROUNDS: each round is 1 of your cards
    vs 1 opponent card (head-to-head). The card with the higher value in the
    CHALLENGE ATTRIBUTE wins that round and earns points.
  - We ONLY play the "Virtual" (3-round, easier) mode — never the 5-round hard mode.
  - After 3 rounds, the player with the MOST POINTS (most rounds won) wins the
    battle and earns ACR tokens and level-up XP.
  - You need to WIN AT LEAST 2 OF 3 ROUNDS — this is the key objective.

CARD SELECTION STRATEGY (which 3 to pick):
  - Always pick the 3 cards with the highest value in the CHALLENGE ATTRIBUTE.
  - Only the relevant attribute matters — ignore overall power for this choice.
  - Tiebreaker: prefer higher-rarity cards (Legendary > Epic > Rare > etc.).

CARD SELECTION AND CONFIRMATION:
  - You NEVER see the opponent's cards before selecting your 3. Selection is
    always blind — choose purely based on your own cards' attribute values.
  - After tapping your 3 cards, tap the confirm/start button to begin the battle.

CARD ORDER STRATEGY (blind — opponent cards are never visible upfront):
  - Always play STRONGEST card first to take an early lead.
  - Play MEDIUM card second to secure or extend the lead.
  - Play WEAKEST card last — the match is usually decided by then.
  - A 2-1 win and a 3-0 win both earn victory XP — a 3-0 sweeps for bonus XP,
    so lead with your best card every time.
  - Never hold back a strong card "in reserve" — there are only 3 rounds.

COMPANY STORE — SPENDING ACR ON BOOSTS:
  - The Company Store sells temporary or permanent BOOSTS using ACR tokens.
  - Example boost: "Coffee Run" costs 65 ACR and gives the whole team +10% power.
  - Boosts directly increase battle win rate → more XP per hour.

  BOOST BUYING RULES:
  1. Calculate value: a +10% power boost for 65 ACR is good value if it flips
     close losses into wins. Estimate how many extra wins it buys.
  2. Buy boosts BEFORE entering a battle, not after losing — timing matters.
  3. Prefer TEAM-WIDE boosts (affect all 3 battle cards) over single-card boosts.
  4. Stack boosts when facing a tough opponent or a challenge attribute where
     your team is weaker than usual.
  5. Keep a reserve of ~100 ACR at all times for emergency boosts mid-session.
  6. Do NOT spend all ACR on boosts if you are close to affording a new
     high-rarity card in recruitment — permanent card upgrades outvalue
     temporary boosts in the long run.
  7. If a boost is cheap (≤50 ACR) and improves a stat you use often, buy it.
  8. Re-read store items each visit — inventory may rotate.

REWARDS & PROGRESSION — THE META-GOAL:
  - THE PRIMARY GOAL IS TO ACCUMULATE XP (Experience Points).
  - XP advances your rank on the CORPORATE LADDER (think promotions:
    Intern → Associate → Manager → Director → VP → C-Suite, etc.).
  - Every battle you play earns XP — wins earn significantly more than losses.
  - ACR tokens are currency spent in the COMPANY STORE or on recruitment.
  - A 3-0 sweep likely earns more XP than a 2-1 win — go for the clean sweep
    when your cards clearly dominate.
  - NEVER skip a battle or quit early — even a loss earns some XP.
  - XP may also come from: completing daily challenges, recruiting new cards,
    levelling up individual cards — always look for these bonus XP sources
    on the screen and prioritise them.

XP OPTIMISATION MINDSET:
  - Volume matters: play as many battles as possible.
  - Win rate matters: a stronger team = more wins = faster XP gain.
  - Invest ACR tokens wisely between the COMPANY STORE (boosts) and
    RECRUITMENT (new cards) — both improve win rate and compound XP earnings.
  - Climbing the corporate ladder may unlock new cards, stronger opponents,
    and higher XP rewards per battle.

=== STRATEGIC PRIORITIES ===
1. MAXIMISE XP PER SESSION — win battles, complete challenges, recruit cards.
2. BATTLE CARD ORDER: Sequence your 3 cards to guarantee winning 2/3 rounds.
   A 3-0 sweep earns more XP — go for it when possible.
3. BATTLE SELECTION: Pick the 3 cards with the highest challenge-attribute value.
4. TEAM SELECTION: Cover all 10 attribute categories so you win more battles.
5. SPEND ACR WISELY between the Company Store (boosts) and recruitment.
6. NEVER idle — if there is a button to press, a card to recruit, or a
   battle to enter, do it. Idle time is lost XP.

NAVIGATION — HOW TO FIND VIRTUAL BATTLES:
  - On the main screen, tap the "Work Remotely" button to open the battle menu.
  - The battle menu shows at least two options — always select "Virtual"
    (3-round, easier). Never tap the harder 5-round option.
  - After selecting "Virtual", a button appears at the BOTTOM of the screen
    labelled "Join Virtual Meeting" — tap it to enter the battle.
  - Primary game loop:
      main screen
        → tap "Work Remotely"
        → tap "Virtual"  (3-round mode)
        → tap "Join Virtual Meeting"  (bottom of screen)
        → team selection (pick 5 cards)
        → battle (3 rounds of 1v1)
        → collect XP + ACR reward
        → tap "Back to Office"  (returns to main screen)
        → repeat indefinitely.
  - If you ever see both mode options and are unsure which is which, pick the
    one labelled "Virtual" or described as "3 rounds" / "Easy".
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
team_management | company_store | recruitment | battle | card_play | result | \
xp_reward | post_battle | corporate_ladder | daily_challenge | loading | unknown>",
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
  "xp_visible": "<XP bar or amount shown on screen, e.g. '1240/2000 XP' or null>",
  "store_items": [
    {{
      "name": "<boost name>",
      "cost_acr": <integer ACR cost>,
      "effect": "<description of what it does>",
      "buy_recommendation": "<buy_now | save_acr | skip>",
      "buy_reason": "<one sentence why>"
    }}
  ],
  "ladder_rank": "<current corporate ladder rank if visible, e.g. 'Manager' or null>",
  "acr_tokens": "<ACR token count if visible, or null>",
  "battle_round": <current round number 1, 2, or 3 — always 3 rounds total in Virtual mode — or null if not in a round>,
  "battle_score": "<score so far e.g. '1-1' or 'You 2 - Opponent 1', or null>",
  "battle_pick_note": "<during card selection: which 3 cards and why, with challenge-attribute values>",
  "battle_order_note": "<during a round: which card to play NOW and why — opponent cards are never visible so always lead strongest → medium → weakest>",
  "team_selection_note": "<only for team_selection/team_management screens: \
explain which 5 you are picking and why they cover diverse skill categories>",
  "opponent_visible": null,
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

        # XP / ladder progress
        rank = result.get("ladder_rank")
        xp = result.get("xp_visible")
        acr = result.get("acr_tokens")
        progress_parts = []
        if rank:
            progress_parts.append(f"Rank: {rank}")
        if xp:
            progress_parts.append(f"XP: {xp}")
        if acr:
            progress_parts.append(f"ACR: {acr}")
        if progress_parts:
            print(f"  Progress: {' | '.join(progress_parts)}")

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
            rnd = result.get("battle_round")
            score = result.get("battle_score")
            suffix = ""
            if rnd:
                suffix += f"  Round {rnd}/3"
            if score:
                suffix += f"  Score: {score}"
            print(f"  Challenge: {challenge}{suffix}")

        battle_note = result.get("battle_pick_note")
        if battle_note:
            print(f"  Battle pick: {battle_note}")

        order_note = result.get("battle_order_note")
        if order_note:
            print(f"  Card order: {order_note}")

        for item in result.get("store_items") or []:
            rec = item.get("buy_recommendation", "?")
            symbol = {"buy_now": "✓", "save_acr": "~", "skip": "✗"}.get(rec, "?")
            print(
                f"  Store [{symbol}] {item.get('name')} "
                f"{item.get('cost_acr')} ACR — {item.get('effect')} "
                f"({item.get('buy_reason')})"
            )

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
