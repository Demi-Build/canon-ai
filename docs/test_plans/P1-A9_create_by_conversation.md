# Human test plan — row P1-A9, create by conversation

**Row:** master §3.1 stage 7, `P1-A9 [B]` · **Gate:** an ice world created by conversation, end to
end, on free backends. **Met 2026-09-01** against the real create path rather than a canned result.
**Design source:** `design_handoff_agent_panel` board 05 and README §11, which A5 deliberately left
as step 10 of the package's order.

## A. The gate

```bash
uv run python -m canon.agent.eval --backend fake --only create-ice-world
```

- [x] **A1** passes at $0. The conversation asks its clarifying questions, proposes a plan, and on
      approval calls the real `create_project` body, so a real tree lands on disk.



## B. In the app, from the start page

- [x] **B1 — Allow mode is disabled with its reason**: grants are per project and no project is
      open. Ask and Plan are available.
- [x] **B2 — creating is a conversation, not a modal.** Describe the game you want. You get at most
      two clarifying questions, then a numbered plan whose button reads `Create · up to $X` beside
      `Edit steps` and `Start blank instead`.
- [x] **B3 — free is free.** With fake and none backends the plan shows $0 and no spend card
      appears. Only a real backend selection raises the accent card.
- [x] **B4 — the folder exists before anything is spent.** Stop mid-create and confirm what exists
      is kept, with an honest report of what landed versus what never started.
- [x] **B5 — one pipeline.** The conversational create and the modal both drive the same command,
      the same StepLog and the same CreateProgress. While it runs, the recents rail shows a live
      project card and the status bar mirrors it.
- [x] **B6 — the world opens editable**, which is only true because rows 6, 8 and 9 shipped first.



## C. Decisions to confirm

- [x] The panel header's Stop now cancels a start-page create rather than merely claiming to. A
      reviewer caught it reporting "stopped by you" while the job ran on. Confirm it behaves.



## D. On approval

- [ ] Say "A9 approved".