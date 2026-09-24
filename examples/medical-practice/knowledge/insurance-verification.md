# Insurance Verification — Workflow SOP
> HYPOTHETICAL EXAMPLE POLICY — fictional practice, invented payers.

## When to verify
- New patient, new plan year, card changed, service type changed
  (e.g. procedure added), or any coverage flagged `UNCONFIRMED`.

## Steps
1. Payer portal **Ridgeline-Link** → Eligibility → member ID + DOB.
2. Confirm: plan active through today, copay tier, PCP assignment,
   and prior-auth requirement for today's service code.
3. Record result in chart: `VERIFIED <payer> <date>` or `FAIL <reason>`.
4. **Effective-date trap**: coverage starting after today's date is a FAIL —
   do not schedule; offer self-pay estimate or later date.
5. FAIL paths: expired ID → patient callback script (form FD-12); portal
   down → phone verification, note rep name + reference #; unresolved by
   end of day → `UNCONFIRMED` and hand to billing by 16:30.
6. Self-pay conversion: estimate sheet **PE-04**, discuss before the visit,
   mention sliding-scale if income-eligible.

## Never
- Promise coverage ("that will be covered") — quote the plan document only.
- Discuss benefits with anyone who isn't the member without an ROF on file.
