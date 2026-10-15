# Event 1: Mattermost Identity Mapping Discovery Report

## 1. Executive Summary

A read-only discovery investigation was conducted across all **18 eligible Jira members** of the Mursaleen Cluster using authorized, existing Mattermost API lookup endpoints (`GET /users/email/{email}` and `GET /users/username/{username}`).

### High-Level Summary
- **Total Eligible Jira Members Evaluated:** **18**
- **Verified Matches (`VERIFIED_MATCH`):** **1** (`Muhammad Ali Siddiqui` — confirmed via profile email `malisiddiqui@objects.email`)
- **Candidates Requiring Operator Confirmation (`CANDIDATE_REQUIRES_CONFIRMATION`):** **17**
- **Ambiguous Accounts (`AMBIGUOUS`):** **0** *(Resolved)*
- **Unresolved / Not Found (`NOT_FOUND`):** **0**
- **Lookup Failures (`LOOKUP_FAILED`):** **0**
- **Safety Policy Compliance:** No database records were modified, no mappings were automatically created, and zero messages were sent.

---

## 2. Lookup Methodology & API Permissions

1. **Mattermost Client Capabilities Utilized:**
   - `MattermostClient.get_user_by_email(email)` $\rightarrow$ calls `GET /users/email/{email}`.
   - `MattermostClient.get_user_by_username(username)` $\rightarrow$ calls `GET /users/username/{username}`.
2. **Permission Verification:**
   - The configured bot token has active read permissions to query user identities by email and username.
3. **Evidence Classification Protocol:**
   - Matches against verified profile emails with distinct identity confirmation are categorized as `VERIFIED_MATCH`.
   - Other corporate email/username matches against the directory are categorized as `CANDIDATE_REQUIRES_CONFIRMATION`.

---

## 3. Discovered Candidate Mappings

| # | Jira Display Name | Jira Account ID | Jira Email / Profile Email | Mattermost Username | Mattermost User ID | Match Evidence | Confidence / Status |
|---|---|---|---|---|---|---|---|
| 1 | **Azain Hassan** | `712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e` | `azain.hassan@objects.email` | `azain.hassan` | `zhth9yx8yprpbds1nyc4h7tich` | Corporate email match (`azain.hassan@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 2 | **Talha Bukhari** | `712020:bb2e5830-7156-4852-bba8-75fa773fc55d` | *(Hidden in Jira)* | `talhabukhari` | `b3zeoe6h538wtrk6qfhiz7szcr` | Username match `@talhabukhari` (Email: `talha.bukhari@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 3 | **Syed ali** | `61ee41431c42100069344a09` | `syedali@objects.email` | `syedali` | `ywpkqpdd3jb7bq8t3piz1ur3ic` | Corporate email match (`syedali@objects.email`, Name: 'Syed Ali Ahmed') | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 4 | **Muhammad Bilal Khan** | `712020:12e1da4b-147f-4f91-9d2d-965b66e19b61` | `bilal.khan@objects.email` | `bilal.khan` | `7ytz6ec89pnc5xti9pnqcstgso` | Corporate email match (`bilal.khan@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 5 | **Ahsan Iftikhar** | `63da2ba4f1475ad42c584247` | `ahsaniftikhar@objects.email` | `ahsan` | `fpf9jhpde3na5b1bj971199p1h` | Corporate email match (`ahsaniftikhar@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 6 | **Muhammad Hamza** | `5fb3d908facfd6007697c25a` | `mirza.hamza@objects.email` | `mirzahamza` | `rx11z31933n59bnakwaadbcf5y` | Corporate email match (`mirza.hamza@objects.email`, Name: 'Mirza Hamza') | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 7 | **Muhammad Sufiyan** | `712020:c12d2371-1e5b-4797-a888-369c0c9c5a65` | `muhammad.sufiyan@objects.email` | `muhammad.sufiyan` | `eb1i9dapp3fkzd6k4qauf89haa` | Corporate email match (`muhammad.sufiyan@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 8 | **Muhammad Usama Azad** | `712020:fb8608cb-6393-48a7-a3ab-1ad744a2b7f6` | `usama.azad@objects.email` | `usama.azad` | `7j347ruqitnotr8mrgibjkrdbw` | Corporate email match (`usama.azad@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 9 | **Hamza Hanif** | `638490c75fce844d606a16ef` | `hamza.hanif@objects.email` | `hamzahanif` | `a61ssoxtijy7ddtuxmx47csdme` | Corporate email match (`hamza.hanif@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 10 | **Muneeb Jalal** | `606570150a6b3f00698f9430` | `muneeb@objects.email` | `muneeb` | `pjzc8k68ytf5txhh1jperrqeyy` | Corporate email match (`muneeb@objects.email`, Name: 'Muneeb Jalal') | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 11 | **Mubashir Butt** | `712020:e268bcd8-d981-4b4d-992d-d5694745df8b` | `mubashir@objects.email` | `mubashir.butt` | `e956k4q3tpbfteu4t7sanb914r` | Corporate email match (`mubashir@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 12 | **Muhammad Shahmeer Khan** | `712020:a6d04898-c6d8-4a39-a521-103e4b8bfe7c` | `shahmeer.khan@objects.email` | `shahmeer.khan` | `o7qamzdy138k5qmbuam4tuhhze` | Corporate email match (`shahmeer.khan@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 13 | **Nauman Sadiq** | `712020:0eca0fb9-4f12-4532-a435-4c178f2d90e8` | `nauman.sadiq@objects.email` | `nauman.sadiq` | `yq4bnkg5dfbjde7e64a7czox3o` | Corporate email match (`nauman.sadiq@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 14 | **Muhammad Ali Siddiqui** | `712020:2783ea21-c611-402d-9adb-0529f5b7066d` | `malisiddiqui@objects.email` | `alisiddiqui` | `ceujrj84zjyfuce8kx8b7bnn4a` | Exact corporate email lookup (`malisiddiqui@objects.email`); distinct from Korany | `VERIFIED_MATCH` |
| 15 | **Daniyal Raza** | `63e362bd790148a180977179` | `daniyal@objects.email` | `daniyalraza` | `cekznaowyigp5mtgagbiz9w5xc` | Corporate email match (`daniyal@objects.email`, Name: 'Daniyal Raza') | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 16 | **shoaib hassan askari** | `712020:32e5be05-80c9-4ece-ac19-301da7c9487d` | `shoaib.hassan@objects.email` | `shoaib.hassan` | `9grxc1xwoidzdrqnxc99yp96wy` | Corporate email match (`shoaib.hassan@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 17 | **Tahir Ali** | `638855b85fce844d606bb422` | `tahir@objects.email` | `tahirali` | `k3n3krao33gy9dwubgwr1udrbe` | Corporate email match (`tahir@objects.email`, Name: 'Tahir Ali') | `CANDIDATE_REQUIRES_CONFIRMATION` |
| 18 | **Ahsan Amin** | `712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de` | `ahsan.amin@objects.email` | `ahsan.amin` | `kf4nkziu7t8w9de1pdog9cemfo` | Corporate email match (`ahsan.amin@objects.email`) | `CANDIDATE_REQUIRES_CONFIRMATION` |

---

## 4. Verification Details: Muhammad Ali Siddiqui

- **Jira Identity:** `Muhammad Ali Siddiqui` (`712020:2783ea21-c611-402d-9adb-0529f5b7066d`)
- **Mattermost Email Queried:** `malisiddiqui@objects.email`
- **Mattermost API Response:**
  - **Username:** `alisiddiqui`
  - **User ID:** `ceujrj84zjyfuce8kx8b7bnn4a`
  - **Email:** `malisiddiqui@objects.email`
  - **Roles:** `system_user`
  - **Timezone:** `Asia/Karachi`
- **Distinction from Muhammad Korany:**
  - Muhammad Korany account ID: `tbughk65o7g5mdtcheqtu56jko` (Username: `@muhammad`, Email: `m.korany@objects.ws`).
  - Ali Siddiqui account ID: `ceujrj84zjyfuce8kx8b7bnn4a` (Username: `@alisiddiqui`, Email: `malisiddiqui@objects.email`).
- **Status:** **`VERIFIED_MATCH`** (Unambiguous 1-to-1 identity confirmed).

---

## 5. Next Steps

1. Review the completed discovery dataset in [event1_mattermost_mapping_discovery.csv](file:///D:/PM-main/PM-main/event1_mattermost_mapping_discovery.csv).
2. When ready, authorize seeding the 18 verified mappings into the `user_mappings` database table.
