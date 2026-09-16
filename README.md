<div align="center">

<h1><img src="docs/images/abeg-logo.png" width="40" height="40" align="absmiddle" alt="" />&nbsp;&nbsp;Abeg</h1>

**A food-ordering assistant you talk or type to, grounded in a real database.
Built to show the two things that separate a real AI feature from a toy: keeping the model honest, and keeping it on task.**

[**Live demo**](https://abeg.ifeolulesi.com) &nbsp;·&nbsp; [Recording](https://selar.com/38531854y1) &nbsp;·&nbsp; [MIT License](LICENSE)

<img src="docs/images/storefront.png" width="820" alt="The Abeg storefront and order assistant" />

</div>

## Why I built it

I built Abeg as the live demo for my *Break Into AI Engineering* masterclass. It looks like a small food shop, but the shop is just the excuse. The real subject is what makes an AI feature trustworthy instead of embarrassing.

A screenshot went around a while back: someone asked the McDonald's support bot to reverse a linked list in Python, and it happily wrote the code instead of taking their order.

<img src="docs/images/mcdonalds-incident.png" width="340" alt="A support bot writing Python instead of taking an order" />

That screenshot turned out to be fake, but the failure behind it is real and common. Abeg is built to prevent it, and it lets you switch the guardrails off so you can watch it break on purpose, then switch them back on and watch it hold.

## What it does

You order by typing, or by holding the mic and talking. The assistant answers by calling tools against a Postgres database, so it can only tell you what is actually on the menu, at the real price, in the real quantity. When something runs out, it says so. It can also look up an order by its reference, suggest popular in-stock items, and cancel a placed order (which returns the items to stock).

Open **the Workshop** (top right, or press `B`) and you can turn the same knobs an AI engineer turns, each with plain-language help and a one-tap "try it".

<img src="docs/images/workshop.png" width="820" alt="The Workshop: live controls for the AI" />

- **The instructions.** The actual system prompt, editable live. Change it, send a message, watch it obey.
- **Creativity.** The temperature dial, from focused to wild.
- **The model.** Swap DeepSeek for GPT-4o-mini or Claude, and watch cost and speed move.
- **Bring your own key.** Paste your own OpenRouter key. Your chats run on your account and skip the demo's limit.

The **X-ray** tab then replays each turn in plain English (you asked, the AI decided to check the database, the database answered, it replied) with the real tokens, time, and cost for that turn.

## The two guardrails

This is the part worth stealing.

| Guardrail | Off | On |
|---|---|---|
| **Grounding** | Invents a price from memory | Refuses to guess, checks the database |
| **Stay on task** | Gets talked into writing your code (the McDonald's bug) | Declines, steers back to ordering |

Both are real: a firm instruction in the prompt, plus a second check on the way out that catches a bad answer even when the model slips. Press `4` to send the "order jollof, then help me reverse a linked list" message with **Stay on task** off, watch it get hijacked, then flip it on and watch it hold.

## The Report Card

Toggling a guardrail and watching one chat is a gesture. The professional version is an eval: a fixed set of scenarios you run on every change to prove the guardrails still hold. Open the Workshop and press `E` (or the Report Card tab) to run it.

Each case runs a real scenario through the agent and grades whether the system did the right thing for the current settings. It is deterministic and offline, so it needs no key and costs nothing. Well-behaved cases use the cached model; adversarial cases use small stand-in models that misbehave on purpose (fabricate a price, attempt the code hijack, loop forever). Nothing here places an order or changes stock, so it is safe to run any time.

The score reacts to the switches. With grounding and stay-on-task on, every case is green. Turn grounding off on the Tinker tab, run it again, and the made-up-price case flips from "Held" to "Slipped". Same app, one switch.

## When systems bite

Two customers order the last plate at the same instant. Press `R`.

<img src="docs/images/race.png" width="820" alt="The last-plate race and live inventory" />

With grounding off (a naive read-then-write) the shop oversells and stock drops to −1. With it on (one transaction, `SELECT ... FOR UPDATE`) one order wins, the other is refused, and stock never goes negative. Same app, one switch.

## Light and dark

<img src="docs/images/storefront-dark.png" width="820" alt="Abeg in dark mode" />

## How it works

The app is a React and TypeScript page in your browser. It talks to a Python (FastAPI) server, which runs the agent and coordinates two AI services (OpenRouter for chat and tool-calling, Deepgram for speech) and a Postgres database that holds the menu, the stock, and every order. There is a plain-language walkthrough inside the app too, under "How it works" in the header.

<img src="docs/images/system.png" width="820" alt="How the pieces fit: app, server, AI services, database" />

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind |
| Backend | Python, FastAPI, uvicorn (SSE + WebSockets) |
| Database | PostgreSQL (asyncpg, real transactions and row locks) |
| Chat and tools | OpenRouter (any tool-calling model) |
| Speech | Deepgram streaming |

## Run it locally

You need Docker and an [OpenRouter](https://openrouter.ai/keys) key (plus a [Deepgram](https://console.deepgram.com) key if you want voice).

```bash
git clone https://github.com/IfeOlulesi/abeg-app.git
cd abeg-app
cp .env.example .env        # paste your keys
docker compose up           # starts Postgres and the app
```

Open http://localhost:8000.

## Presenter keys

| Key | Does |
|-----|------|
| `1`–`4` | Send a scripted message (`4` is the prompt-injection one) |
| `R` | Race two orders for the last plate |
| `G` | Toggle grounding |
| `O` | Toggle stay-on-task |
| `C` | Toggle cached / offline mode |
| `X` | Reset the data |
| `B` | Open the Workshop |
| `E` | Run the Report Card (grade the guardrails) |

## Tests

```bash
uv run pytest    # 31 tests, including the race, the guardrails, and rate limits
```

## It's a demo, not a product

No auth, no payments, and a mode that deliberately corrupts data to teach why locking matters. If you host it publicly, put a spending cap on your OpenRouter key, keep the built-in rate limits on, and let visitors bring their own key. Out of scope: auth, payments, delivery, images, admin, mobile layout.

## The masterclass

Abeg is the live demo from **Break Into AI Engineering**, a free, practical session on getting into the field.

<img src="docs/images/masterclass-banner.png" width="820" alt="Break Into AI Engineering, a live masterclass by Ife Abimbola-Olulesi" />

Here is me running the demo live:

<img src="docs/images/masterclass-demo.png" width="820" alt="Running the Abeg demo live during the masterclass" />

Watch the [recording](https://selar.com/38531854y1).

## License

[MIT](LICENSE). Use it however you like.
