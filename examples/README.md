# Examples

## Sample input

[`sample-intake.md`](sample-intake.md) is a realistic example of what you paste into the intake box: messy, partial, in no particular order — an email thread and some meeting notes, not a filled-in form. That's the intended input shape. The Intake agent's whole job is to turn something like this into structured answers for q1–q7.

To try it:

1. Start the app (`python server.py`) and create a project.
2. Paste the contents of `sample-intake.md` into the intake box.
3. Click **Run**.

## Sample output

Not checked in. A full run takes ~50 minutes and produces a 57-question PRD specific to whatever you pasted, so a committed sample would be a snapshot of one run against one input, on one model, at one point in time — and stale within a release or two.

More to the point: a hand-written "example output" would show you writing quality this tool did not produce. If you want to see what it generates, run it on the sample above and read `artifacts/<project-id>/final-prd.md`.

If you run it and think the output is worth showing others, a PR adding it here — with the model and date it was generated on — is welcome.
