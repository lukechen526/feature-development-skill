# Recovery

1. Re-derive committed view (`lane.md` § Derived state); inspect surviving worktrees/refs.
2. Missing dispatch/review record: redo affected work/review, never infer completion/approval.
   Unprovable current human approval: repeat checkpoint.
   Missing decision records are not recoverable from scratch: the standard review gate applies.
   Reused ancestor verdicts need their recorded ancestor commit, intervening-diff binding, and
   unchanged evidence inputs; a broken binding reverts that duty to the standard review gate.
   Human authorization and combined-Oracle approvals are never reused or recovered by ancestry.
   A deferred-to-integrated decision is not an approval: recover its exact lane commit and
   eligibility or require the early lane checkpoint. Lost directed human inspection checkpoints
   must be repeated personally; a changed integrated HEAD or inspection record cannot be
   repaired by Oracle stand-in or by a preauthorized PR approval.
3. Before same-worktree redispatch, use dispatch key to prove prior session absent, stopped, or released.
   Unknown session: stop for human; never start second writer.
4. Reuse `PASS` only after checking clean tree, exact reviewed `HEAD`, and existing evidence.
5. Check ancestry and recorded intents; never double-dispatch, double-merge, or double-remove.
   Combined review also requires recorded base, candidate, acceptance/evidence bindings, and exact post-merge `HEAD`.
   Equal trees/ancestry alone never prove approval.

Preserve reports/evidence under absolute change directory.
Block on missing/stale binding, unknown worker, unapproved changed plan, failed required check,
unresolved Oracle escalation, or unmet human gate. Account for all workers/worktrees/sessions/temp resources before stopping.

Prefer completion events; bound fallback polling/output. Reuse preflight only while mechanism/model/level/environment/evidence bindings match.
