# Agent Mail

Store agent messages and receipts in one SQLite mailbox per organization. Deliver short notices through native Herdr.

## Install

Copy this directory to `<agentorg>/tools/agent-mail/`. Python 3.8 or newer with SQLite and a local Herdr installation are required. Make sure `agent-mail` is executable and use its absolute path or add its directory to `PATH`.

The executable resolves its installation path independently of the working directory. It creates `<agentorg>/organization-state/agent-mail/mailbox.sqlite3` on first use. The CLI also uses SQLite sidecars and a notice lock in that directory. Keep this state directory on a local filesystem accessible to the participating agent sessions. Copy the tool directory alone to give another organization a fresh mailbox. Use SQLite's backup facility to back up an open mailbox.

Each sending or receiving agent runs the CLI from a named Herdr pane. The CLI matches the caller's socket to the running Herdr session and gets the live agent name from its pane. Recipient addresses use `TEAM/SESSION/AGENT`. Team labels are supplied for tracking. Herdr verifies the session and agent name. The human views are available from a regular shell. This mailbox assumes mutually trusted users and agents with access to the local state directory.

## Send

```sh
agent-mail send \
  --from-team blue \
  --to blue/studio/build-agent \
  --project demo --task 'demo#12' --kind handoff \
  --subject 'Demo handoff' --from-file /path/to/handoff.md
```

Use `--to` once per recipient. Cross-team mail uses each recipient's actual team and Herdr session in its address. The optional project, task and kind describe the message for the human view. Use `--reply-to M42` to link a reply. Use `--body` to supply new text or `--from-file` to store an exact UTF-8 snapshot of an existing handoff.

For a complete one-line update, use `--action ack --subject 'READY / HOLD — waiting for lead GO'`. The subject is the stored message in that case.

## Receive

The CLI constructs the entire Herdr notice:

```text
MAIL M42 read | Demo handoff
MAIL M43 ack | READY / HOLD — waiting for lead GO
```

- For `read`, run `agent-mail read M42`, review the body, then run `agent-mail ack M42`.
- For `ack`, the subject is the update. Run `agent-mail ack M43`.
- `agent-mail inbox` lists unacknowledged notices for the current Herdr agent. A receipt confirms that the notice was received. Existing authorization and holds govern any follow-on work.

The agent commands return compact notices, exact message bodies or receipt status. Use the human views for tracking metadata.

## Inspect and recover

```sh
agent-mail history --team blue --project demo
agent-mail history --from-team green --to-team blue
agent-mail history --task 'demo#12' --from review-agent --to build-agent
agent-mail show M42
agent-mail status M42
```

`--team` matches either direction. History shows one row per recipient and uses the shell's local timezone. `show` displays the message, reply links, sender identity, and each recipient's delivery timestamps and latest notification error.

The CLI commits the body and all recipient rows before contacting Herdr. `submitted` records Herdr's acceptance. `acknowledged` records the recipient's separate receipt. `pending`, `rejected`, and `uncertain` messages stay visible in the inbox and make `send`, `notify`, or `status` return exit code 1. Exit code 2 means a command, identity, or storage error.

If an attempt fails, check its exact ID with `status` or `show`. The original sender can run `agent-mail notify M42` to retry pending or rejected notices. For uncertain submission, check for receipt first and add `--retry-uncertain` if redispatch is needed. A retry reuses the same message ID, and the CLI skips recipients already acknowledged or submitted. If a notice appears twice, use its message ID to recognize the same message before taking action. Repeated acknowledgments preserve the original receipt time.

Any reply or action follows the existing organization workflow. Use Kanzo for task ownership and status.
