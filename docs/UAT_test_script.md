# UAT test script: Trade confirmation template TC-v3.1

**Change:** Trade confirmations move from template TC-v2.9 to TC-v3.1, which adds the mandatory clause *"Please report any discrepancy within 24 hours of receipt…"*. The same release moves settlement to T+1.

**Environment:** `python -m commsqa serve` with the bundled sample data (seed 5).
**Sign-off required from:** Operations (business owner), Compliance (clause wording), Technology (release).

| ID | Scenario | Steps | Expected result | Automated by |
| --- | --- | --- | --- | --- |
| UAT-01 | Current template carries all mandatory clauses | Open any trade confirm issued from week 5 onwards | Terms clause, 24-hour discrepancy clause and grievance clause present; footer shows `Template TC-v3.1`; no R11 or R12 defect | R11, R12 |
| UAT-02 | Old template is detected | Filter the defect log by rule R11, week 2026-W32 | Every confirm printed from TC-v2.9 is flagged *Mandatory clauses present* (High, Template) | R11; `test_template_incident_ends_after_week_four` |
| UAT-03 | Settlement is T+1 | Open a confirm with a Friday trade date | Settlement date is the following Monday | R07; `test_t_plus_one_skips_weekends_and_holidays` |
| UAT-04 | Settlement skips a holiday | Trade dated 2026-10-01 (Thursday before Gandhi Jayanti) | Settlement 2026-10-05 | `test_t_plus_one_skips_weekends_and_holidays` |
| UAT-05 | Figures add up | Any confirm: check gross = quantity × price, GST = 18% of brokerage, net = gross ± charges | No R06 defect on a correct document; a transposed net amount raises R04 and R06 | R04, R06 |
| UAT-06 | Account number is masked | Any document | Only the last 4 digits are shown; a full account number is a Critical R03 defect | R03 |
| UAT-07 | Amounts in Indian format | Confirm with gross over 1 lakh | Printed as `1,23,456.78`, and read back by QA without error | `test_indian_number_format_round_trips` |
| UAT-08 | Reviewer decision is kept | Mark a defect *Not a defect* with a note, then click **Re-run checks** | The defect keeps status REJECTED, the reviewer name and the note; accuracy excludes it | `test_review_decisions_survive_a_rerun` |
| UAT-09 | Scorecard export | Click **Download Excel scorecard** | Workbook opens with Summary, Weekly trend (2 charts), Pareto (chart) and a filterable Defect log | manual |
| UAT-10 | Regression: no false alarms | Run `python -m commsqa run` | Self-test shows 100% of seeded defects caught and 0 clean documents flagged | `test_every_seeded_defect_is_caught_with_no_false_alarms` |

**Exit criteria:** all scenarios pass, with no open Critical or High defects attributable to the new template.
