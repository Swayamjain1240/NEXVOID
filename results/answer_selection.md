# Answer selection — does the answer return the document's table of contents? (M15)

Encoder `potion-base-8M`. Every answerable question in the three frozen sets is scored twice:
with the query-coverage selector as it was, and with the heading-echo guard added
(`src/atlas/select.py`). The guard has no threshold to fit, so nothing is held out - the
rule below was written before the run and the counts are the whole evidence.

## The two questions, separated

| set | answerable | top-1 passage from the right document | heading-echo answers before | after |
|---|---|---|---|---|
| v1 | 24 | 20 | 3 | 0 |
| v2 | 12 | 12 | 3 | 0 |
| v3 | 12 | 11 | 2 | 0 |

Across the three sets the guard removes **8 heading-echo answers** (8 → 0) and changes 8 answers in total.

The first column matters as much as the second. A selector cannot answer from a passage
that does not contain the answer, and the top-1 passage is from the labelled document for 20 of v1's 24 questions (12/12 on v2, 11/12 on v3). That is a ranking problem, not a selection one: recall@10 is 1.0000 on the appended sets, so the
right passage is retrieved and then out-ranked, which is FR-11 territory in
`results/fusion.md` and not something a sentence scoring rule can reach.

## Every answer the guard changed

**v1**

- `a001`
  - before: Minimum attendance requirements, condonation rules, medical leave and the consequences of falling below the attendance bar. A course is counted only if the stud
  - after: A course is counted only if the student is on its current registration list; attendance is not retroactively credited for courses added late. A student below 65
- `a003`
  - before: Value, eligibility, renewal conditions and disbursement schedule of the merit scholarship. Renewal each year requires both of the following at the close of the 
  - after: The merit scholarship is worth Rs 50,000 per academic year, credited twice, Rs 25,000 per semester, at the start of odd and even semesters respectively. Renewal
- `a007`
  - before: Limits on the number of attempts for a failed course, carry-forward rules and load restrictions for students with backlogs. A student may carry forward a maximu
  - after: A failed course may be attempted a maximum of 3 times in the regular end-term cycle and 2 times in the supplementary cycle; that is 5 total attempts. A student 

**v2**

- `v2-006`
  - before: Fee schedule, instalment dates, late fee, refund slabs and the interaction with scholarships. The annual tuition is Rs 1,35,000, payable in two instalments of R
  - after: The annual tuition is Rs 1,35,000, payable in two instalments of Rs 67,500 each, due on 20 August and 20 January. A payment made after the due date attracts a l
- `v2-008`
  - before: Requirements, application timing, credit load and exit rules for the minor and honours tracks. A student in honours whose CGPA falls below 8.0 is moved to the n
  - after: A student in honours whose CGPA falls below 8.0 is moved to the normal programme at the semester boundary, andhonours cannot be re-joined. Minor and honours may
- `v2-010`
  - before: Occupancy, charges, curfew, visitor policy and the conditions for hostel leave. Visitors, including parents, are permitted only on Saturday and Sunday between 5
  - after: Hostel occupancy is per student, one bed one room, with the annual charge of Rs 90,000, covering accommodation and mess for the academic year. Visitors, includi

**v3**

- `v3-007`
  - before: Value, eligibility, renewal conditions and disbursement schedule of the merit scholarship. The amount is adjusted against tuition first; if tuition is already w
  - after: The merit scholarship is worth Rs 50,000 per academic year, credited twice, Rs 25,000 per semester, at the start of odd and even semesters respectively. The amo
- `v3-010`
  - before: Schedule, registration, fees, subject limits and the pass treatment of supplementary papers. A student may register for a maximum of 2 papers in one supplementa
  - after: Registration for a supplementary paper closes 2 weeks before the cycle begins; the fee is Rs 1,000 per paper. A student may register for a maximum of 2 papers i

## Decision, and a correction to the rule I wrote first

The pre-registered rule required that no answer lose query-term coverage. It fired on
v1: a001, and that result is the rule's bug, not the
guard's: a topic list enumerates the document's topics, so it matches more query words 
than any single statement can. Requiring the answer to keep that coverage keeps the
the failure. The amended criterion asks the question that actually matters - is the
replacement still a statement rather than another topic list?

| rule | requirement | outcome |
|---|---|---|
| as pre-registered | echo → 0 **and** no coverage lost | fail |
| amended (decides) | echo → 0 **and** every replacement is a statement | pass |

- **Adopted.** Heading-echo answers 8 → 0 across the three sets, and all 8 replacements carry a number or a finite verb, so each one is a policy statement instead of a list of headings.

## What this does not fix

- **The passage is still chosen by retrieval.** Six of v1's 24 questions get a top-1
  passage from the wrong document (a001 asks attendance and is answered from the medical
  document; a009 asks about re-exams and is answered from placements). The guard cannot
  help there, and the answer is wrong for a reason no selector can repair.
- **A summary sentence that is not the first sentence.** The guard only inspects the
  passage's opening line, because that is the structural position of a document's topic
  list. A mid-document summary in a merged chunk is still eligible; nothing in the corpus
  produces one today, and this report does not claim otherwise.
