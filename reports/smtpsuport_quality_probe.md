# Historical Jira Data Quality Diagnostic Report

- **Probe ID**: `probe-9ceeed9e`
- **Timestamp**: `2026-09-30T03:42:04.765983+00:00`
- **Evaluated Projects**: `SMTPSUPORT`
- **Lookback Days**: `90`
- **Max Cap Per Project**: `200`

## Project: `SMTPSUPORT`

**Total Sampled Issues**: 73 (*(Complete set in range)*)

| Metric | Count | Coverage % | Status |
| :--- | :---: | :---: | :--- |
| Valid Created Timestamp | 73 | 100.0% | Good |
| Valid Resolved Timestamp | 73 | 100.0% | Good |
| Valid Updated Timestamp | 73 | 100.0% | Good |
| Valid Lifecycle Pair (Start & End) | 73 | 100.0% | Good |
| Issue Type Populated | 73 | 100.0% | Good |
| Priority Populated | 73 | 100.0% | Good |
| Assignee Populated | 61 | 83.6% | Good |
| Components Populated | 0 | 0.0% | Sparse |
| Labels Populated | 44 | 60.3% | Moderate |
| Parent / Epic Linked | 0 | 0.0% | Sparse |
| Original Estimate Available (>0) | 0 | 0.0% | Sparse |
| Time Spent Available (>0) | 60 | 82.2% | Good |
| Issues With >=1 Worklog | 60 | 82.2% | Good |
| Changelog / Transitions Available | 73 | 100.0% | Good |
| Changelog Estimate Changes | 60 | 82.2% | Good |
| Changelog Reopen Transitions | 9 | 12.3% | Sparse |

### Lifecycle Duration vs. Actual Logged Effort
- **Elapsed Wall-Clock Lifecycle (Created to Resolved)**: Avg `4385.5h`, Median `3379.3h` (73 issues)
- **Actual Logged Worklog Effort**: Avg `3.0h`, Median `1.2h` (60 issues)
- **Total Worklog Logged Time**: `181.48 hours`
> [!NOTE]
> Elapsed lifecycle time (created-to-resolved duration) measures wall-clock calendar lead time and includes queue wait/idle time. It must NEVER be conflated with logged developer effort.

### Identified Limitations
- Original estimates are sparse (0.0% coverage). Planned vs. actual variance analysis cannot rely on Jira original estimates alone.
- Component tags are sparsely populated (0.0%). Domain/skill breakdown will rely primarily on issueType or summary NLP classification.

### Feasibility Recommendation: **READY**
_Strong historical worklog and timestamp coverage in project SMTPSUPORT. Supports evidence-based effort and cycle time estimation prototypes._
