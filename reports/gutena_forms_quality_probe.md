# Historical Jira Data Quality Diagnostic Report

- **Probe ID**: `probe-ef97f598`
- **Timestamp**: `2026-09-30T07:21:50.803308+00:00`
- **Evaluated Projects**: `GF`
- **Lookback Days**: `90`
- **Max Cap Per Project**: `200`

## Project: `GF`

**Total Sampled Issues**: 123 (*(Complete set in range)*)

| Metric | Count | Coverage % | Status |
| :--- | :---: | :---: | :--- |
| Valid Created Timestamp | 123 | 100.0% | Good |
| Valid Resolved Timestamp | 123 | 100.0% | Good |
| Valid Updated Timestamp | 123 | 100.0% | Good |
| Valid Lifecycle Pair (Start & End) | 123 | 100.0% | Good |
| Issue Type Populated | 123 | 100.0% | Good |
| Priority Populated | 123 | 100.0% | Good |
| Assignee Populated | 66 | 53.7% | Moderate |
| Components Populated | 0 | 0.0% | Sparse |
| Labels Populated | 5 | 4.1% | Sparse |
| Parent / Epic Linked | 0 | 0.0% | Sparse |
| Original Estimate Available (>0) | 3 | 2.4% | Sparse |
| Time Spent Available (>0) | 96 | 78.0% | Good |
| Issues With >=1 Worklog | 96 | 78.0% | Good |
| Changelog / Transitions Available | 123 | 100.0% | Good |
| Changelog Estimate Changes | 96 | 78.0% | Good |
| Changelog Reopen Transitions | 2 | 1.6% | Sparse |

### Lifecycle Duration vs. Actual Logged Effort
- **Elapsed Wall-Clock Lifecycle (Created to Resolved)**: Avg `1272.3h`, Median `449.6h` (123 issues)
- **Actual Logged Worklog Effort**: Avg `3.9h`, Median `2.7h` (96 issues)
- **Total Worklog Logged Time**: `375.15 hours`
> [!NOTE]
> Elapsed lifecycle time (created-to-resolved duration) measures wall-clock calendar lead time and includes queue wait/idle time. It must NEVER be conflated with logged developer effort.

### Identified Limitations
- Original estimates are sparse (2.4% coverage). Planned vs. actual variance analysis cannot rely on Jira original estimates alone.
- Component tags are sparsely populated (0.0%). Domain/skill breakdown will rely primarily on issueType or summary NLP classification.

### Feasibility Recommendation: **READY**
_Strong historical worklog and timestamp coverage in project GF. Supports evidence-based effort and cycle time estimation prototypes._
