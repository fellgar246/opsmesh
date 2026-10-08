# Workflows

`approval_wait.asl.json` is the approval wait. It is a deterministic state machine: waiting, approved, rejected, expired, draft created, and notified.

Agent planning finishes before a proposal exists. This machine calls the approval service and the draft service. A matching approval creates one draft and publishes a notification. A rejection or an expiry creates no draft. The wait is the approval expiry, 72 hours. The local profile runs the same transitions. Amazon Step Functions uses this definition only when the managed state machine is on.
