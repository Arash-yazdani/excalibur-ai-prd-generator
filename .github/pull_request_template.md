**What this changes**

<!-- One or two sentences. Link an issue if there is one. -->

**Why**

<!-- What problem this solves. If it's a prompt change, say what the output looked
     like before and after, prompt edits are hard to review from the diff alone. -->

**Checklist**

- [ ] `ruff check .` passes
- [ ] `pytest` passes
- [ ] If question ownership or `framework/questions.json` changed, the framework invariant tests still pass
- [ ] No new runtime dependency (or the PR explains why it replaces more code than it adds)
