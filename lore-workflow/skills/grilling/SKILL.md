---
name: lore-workflow:grilling
description: Interview the user relentlessly about a plan or design. Use when the user wants to stress-test a plan before building, or uses any 'grill' trigger phrases.
---

**Tier note:** this step runs in the main session at frontier-tier — it is not delegated to a
subagent.

Interview the user relentlessly about every aspect of this plan until you reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Show the user the questions you have in one list with a short explanation first 1-2 lines max and numbered. Then user can opt to align on the questions by giving their context up-front shortening the grill. Do not take input that is ambigous or confusing at face value but ask back if you need to clarify more or the questions were not understood correctly. 

If opts to grill slow: 

Ask the questions one at a time, waiting for feedback on each question before continuing. Asking multiple questions at once is bewildering.

Else: if all questions solved to satisfaction, check if /lore-workflow:domain-modeling is needed and if need be align with the user before updating the domain model then if all questions and domain modle updates are satisfactory say so and invoke /lore-workflow:to-epic. 

If a question can be answered by exploring the codebase, explore the codebase instead.
