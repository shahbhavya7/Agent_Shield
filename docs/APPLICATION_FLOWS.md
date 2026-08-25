# AgentShield — Flowcharts of Every Flow

This document is pure flowcharts — every screen-to-screen journey a user takes, and every
step-by-step process happening behind the scenes. For what each concept actually *means* and
how it was built, see the companion document `docs/APPLICATION_GUIDE.md`. Read that one for
the "what and why," and this one for the "what happens, in what order."

All diagrams use plain-English labels, no code or technical jargon inside the boxes.

---

## 1. The whole app, at the highest level

```mermaid
flowchart TD
    A[Land on the homepage] --> B{What do you want to do?}
    B -->|Test a brand-new chatbot| C[Connect a New Agent flow]
    B -->|Test a chatbot already set up before| D[Test Existing Agent flow]
    B -->|Just want to see what a report looks like| E[View Sample Report]

    C --> F[Review Test Cases]
    D --> F
    F --> G[Run the Test]
    G --> H[See the Report]
    E --> H
```

---

## 2. Flow: Connecting a brand-new chatbot

This is the path when someone has their own chatbot and wants to test it for the first time.

```mermaid
flowchart TD
    A[Click Start Testing on homepage] --> B[Choice screen: New Agent or Existing Agent?]
    B -->|Start New Agent Test| C[Connect Your AI Agent screen]

    C --> D[Type in: agent name, chat URL, optional API key]
    D --> E{Provide background info about the agent?}
    E -->|Upload a document| F[File is read and attached]
    E -->|Type a short description| G[Description saved]
    E -->|Skip it| H[AgentShield will figure it out itself later]

    F --> I[Click Verify Connection]
    G --> I
    H --> I

    I --> J[Step 1: Register the agent in the system]
    J --> K[Step 2: If a document was uploaded, save it]
    K --> L[Step 3: Send a real test message to the agent]
    L --> M{Did it respond successfully?}
    M -->|No| N[Show error, go back to Connect screen]
    N --> C
    M -->|Yes| O{Was background info given?}
    O -->|No| P[Step 4: Ask the agent a few icebreaker questions to guess its domain]
    O -->|Yes| Q[Skip the icebreaker step]
    P --> R[Verified! Move to Configure Test screen]
    Q --> R
```

---

## 3. Flow: Picking a chatbot you've already tested before

```mermaid
flowchart TD
    A[Choice screen] -->|Test Existing Agent| B[List of Customers and their Agents]
    B --> C[Load the list from the system]
    C --> D[Show a table: one row per Customer + Agent combination]
    D --> E{How do you want to pick?}
    E -->|Check one or more boxes, click Run Selected| F[Only the checked ones are chosen]
    E -->|Click Run All Agents| G[Every agent in the list is chosen]
    F --> H[Skip any that aren't actually set up yet, warn if so]
    G --> H
    H --> I[Go straight to Review Test Cases screen<br/>Connect + Verify are skipped]
```

---

## 4. Flow: Loading test cases for the Review screen

This happens automatically right when you land on the Review screen, whichever path got you
there.

```mermaid
flowchart TD
    A[Arrive at Review Test Cases screen] --> B{Coming from Connect New Agent,<br/>or from Existing Agent list?}

    B -->|New Agent| C[No test cases exist yet]
    C --> D[Ask AI to generate 8-10 test cases<br/>based on chosen categories + agent info]
    D --> G[Show the generated test cases]

    B -->|Existing Agent, one or more chosen| E[For EACH chosen agent, at the same time:]
    E --> F{Were test cases already saved<br/>for this agent before?}
    F -->|Yes| F1[Load the saved ones]
    F -->|No| F2[Ask AI to generate a fresh batch]
    F1 --> G
    F2 --> G

    G --> H[User reviews / edits / adds / deletes test cases]
```

---

## 5. Flow: The Review Test Cases screen in detail

This is the most interactive screen in the app. Here's everything a user can do on it.

```mermaid
flowchart TD
    A[Review Test Cases screen] --> B{What does the user want to do?}

    B -->|Edit an existing test case| C[Open Edit form, pre-filled]
    C --> D[Change title / category / fault / messages / expected behavior]
    D --> E[Save changes — marked as edited]
    E --> A

    B -->|Add a test case by hand| F[Open blank Add form]
    F --> G[Type everything manually]
    G --> H[Add to the list — marked User-added]
    H --> A

    B -->|Add a test case using AI| I[Open Add form, switch to Generate with AI]
    I --> J[Type a short description of what to test]
    J --> K[AI drafts a full test case]
    K --> L[Form switches back to normal editing,<br/>pre-filled with the AI draft]
    L --> M[Review / tweak / then Add to suite]
    M --> A

    B -->|Delete a test case| N[Removed immediately from the list on screen]
    N --> A

    B -->|Regenerate all test cases| O[Ask: keep old ones and add new,<br/>or replace everything?]
    O -->|Keep and add| P[New AI test cases added to the existing list]
    O -->|Replace all| Q[Old list thrown away, replaced with a fresh AI batch]
    P --> R[Automatically saved to the system]
    Q --> R
    R --> A

    B -->|Save Test Cases button| S[Current list saved to the system,<br/>nothing is run yet]
    S --> A

    B -->|Run Reliability Test button| T[Everything on screen is saved first,<br/>then the actual test run begins]
```

---

## 6. Flow: Running the test (single chatbot)

```mermaid
flowchart TD
    A[Click Run Reliability Test] --> B[Save the final test case list]
    B --> C[Start the run]
    C --> D[Show a live progress screen]
    D --> E[Check progress every couple seconds]
    E --> F{Finished yet?}
    F -->|Not yet| E
    F -->|Yes| G[Fetch the full report]
    G --> H[Show the Report screen]
```

---

## 7. Flow: Running the test on multiple chatbots at once

```mermaid
flowchart TD
    A[Click Run N Agents in Parallel] --> B[Save every agent's test case list]
    B --> C[Launch all of them together as one batch]
    C --> D[Show ONE shared progress screen]
    D --> E[Combined progress bar for the whole batch]
    D --> F[A separate progress row for EACH agent,<br/>updating independently]

    F --> G{Check status of the whole batch<br/>every couple seconds}
    G -->|Still some agents running| G
    G -->|Every agent is done or errored| H[Fetch the full report for every agent]
    H --> I[Show the Report screen<br/>with a switcher to flip between agents]
```

---

## 8. Backend process: how parallel runs are kept from overloading the server

This is *how* section 7 is made safe behind the scenes — the "two bouncers" system that stops
too much work from happening at the exact same moment.

```mermaid
flowchart TD
    A[A batch of chatbots is launched] --> B{Bouncer 1: how many chatbots<br/>are already actively running right now?}
    B -->|Already at the limit e.g. 3| C[This chatbot waits in line]
    B -->|Under the limit| D[This chatbot is allowed to start]
    C --> B

    D --> E[Inside that chatbot's run,<br/>many test cases want to play out at once]
    E --> F{Bouncer 2: how much total AI/network<br/>work is happening server-wide right now?}
    F -->|Already at the limit e.g. 6| G[This piece of work waits in line]
    F -->|Under the limit| H[This piece of work is allowed to proceed]
    G --> F

    H --> I[Test case plays out: message sent,<br/>reply + trace recorded]
    I --> J[Slot freed up — next waiting piece of work gets a turn]
    J --> F

    D --> K[Once this chatbot's whole run finishes]
    K --> L[Slot freed up — next waiting chatbot gets a turn]
    L --> B
```

**Why two bouncers and not one:** Bouncer 1 controls how many *chatbots* are active at once.
Bouncer 2 separately controls how much actual *work* (one test case's message, one AI judging
call, etc.) is happening across the **whole server**, no matter how many chatbots are
technically "running." Without Bouncer 2, even a small number of active chatbots could each
try to fire off many test cases simultaneously, silently adding up to far more simultaneous
AI/network calls than intended. Both bouncers are always checked in the same order (chatbot
slot first, then work slot) — checking in a consistent order is what prevents two pieces of
work from getting stuck each waiting on a slot the other one is holding.

---

## 9. Flow: Reading the Report screen

```mermaid
flowchart TD
    A[Report screen loads] --> B{More than one agent in this run?}
    B -->|Yes| C[Show a row of agent pills, each with its own score]
    C --> D[Click a pill to switch which agent's report is shown]
    B -->|No| E[Just show that one agent's report]
    D --> F[Full report view]
    E --> F

    F --> G[Big score ring 0-100, color-coded]
    F --> H[Performance numbers: speed, cost, tokens used]
    F --> I[Bar chart: pass rate by test category, worst first]
    F --> J{Any failures?}
    J -->|Yes| K[One card per failure:<br/>what went wrong, severity, one-line fix, evidence, full transcript]
    J -->|No| L[Show a No Failures Detected celebration banner]
    F --> M[Recommendations: every unique fix, deduplicated]
    F --> N[Passing test cases too, collapsed by default]
```

---

## 10. Backend process: what actually happens during one "Run"

This is the behind-the-scenes version of stages 4 and 5 from the guide — what the system does
internally once you click Run, for one single chatbot.

```mermaid
flowchart TD
    A[A Run begins for one chatbot] --> B[Take the final, reviewed list of test cases]
    B --> C[Save a permanent snapshot of them for this run]
    C --> D[Play out EVERY test case at the same time]

    D --> E[For each test case:]
    E --> F[Send the first scripted message to the chatbot]
    F --> G{Does this test case have<br/>an injected fault?}
    G -->|Yes, a chatbot-cooperative fault| G1[Tell the chatbot to pretend to fail<br/>e.g. tool timeout, stale info]
    G -->|Yes, a fake system outage| G2[Never even contact the chatbot —<br/>pretend its server is down]
    G -->|No fault| G3[Just send it normally]
    G1 --> H[Record the chatbot's reply + its internal trace]
    G2 --> H
    G3 --> H

    H --> I{More scripted messages left?}
    I -->|Yes| F
    I -->|No, but this category allows one extra push| J[AI generates one more escalating message]
    J --> F
    I -->|No more messages at all| K[This conversation is done]

    K --> L[Once ALL conversations are done:]
    L --> M[Judge every conversation]
    M --> N{Did it pass or fail?}
    N -->|Passed| O[Move on]
    N -->|Failed| P[Automatically explain what went wrong + suggest a fix]
    O --> Q[Calculate the overall score]
    P --> Q
    Q --> R[Run marked as Done — report is ready]
```

---

## 11. Backend process: judging one conversation

```mermaid
flowchart TD
    A[One finished conversation] --> B{Was this a fake system outage test?}
    B -->|Yes| C[Skip AI judging entirely]
    C --> D[Use a fixed, pre-decided severity and reason<br/>a broken server is never the chatbot's fault]
    D --> Z[Verdict recorded]

    B -->|No| E[Ask the judge AI to score 4 things:<br/>accuracy, safety, hallucination, recovery]
    E --> F{Did the chatbot honestly say<br/>"I don't know" instead of guessing?}
    F -->|Yes| G[That counts as GOOD behavior, not a failure]
    F -->|No| H[Score normally based on what it actually said]
    G --> I[Recalculate the real pass/fail decision<br/>from the 4 scores using fixed rules —<br/>never just trust the AI's own opinion]
    H --> I
    I --> J{Did it fail?}
    J -->|Yes| K[Decide how severe: low, medium, or high<br/>any leaked secret is always High]
    J -->|No| Z
    K --> Z
```

---

## 12. Backend process: turning judged conversations into one score

```mermaid
flowchart TD
    A[All conversations in this run are judged] --> B[Look at every FAILED conversation]
    B --> C[Add up penalty points:<br/>Low = 1, Medium = 2, High = 3]
    C --> D[Total penalty points]
    D --> E[Score = 100 minus how much of the<br/>maximum possible penalty was actually hit]
    E --> F[Also group failures by category<br/>safety / hallucination / accuracy / recovery / system]
    F --> G[Also group by test type<br/>how many injection tests passed vs failed, etc.]
    G --> H[Also add up performance stats<br/>average speed, worst-case speed, total cost, total tokens]
    H --> I[Final Report is complete]
```

---

## 13. Backend process: connecting a new chatbot (the "black box" handshake)

```mermaid
flowchart TD
    A[User submits: name + URL + optional info] --> B[Save this chatbot as a new entry]
    B --> C{Did the user upload a document?}
    C -->|Yes| D[Attach it permanently to this chatbot's record]
    C -->|No| E[Skip]
    D --> F[Send one real test message to the chatbot's URL]
    E --> F
    F --> G{Did it reply successfully?}
    G -->|No| H[Tell the user the connection failed]
    G -->|Yes| I{Did the user give a description<br/>or upload a document?}
    I -->|No| J[Ask the chatbot 3 neutral icebreaker questions<br/>e.g. "what can you help me with?"]
    J --> K[Show those answers to an AI<br/>and ask it to guess the chatbot's domain]
    K --> L[Save that guess as the description]
    I -->|Yes| M[Skip guessing — info was already given]
    L --> N[Chatbot is fully connected and ready]
    M --> N
```

---

## 14. Backend process: generating test cases

```mermaid
flowchart TD
    A[Request: generate test cases for this chatbot] --> B[Gather everything known about the chatbot:<br/>uploaded docs > written description > auto-guessed description]
    B --> C{Did the user type any special guidance?<br/>e.g. "focus on refund edge cases"}
    C -->|Yes| D[Make sure at least 3 test cases target that guidance]
    C -->|No| E[Skip]
    D --> F[Ask the AI to write 8-10 test cases<br/>covering the requested categories]
    E --> F
    F --> G{Did the AI return at least 4 usable test cases?}
    G -->|No, it failed or returned too few| H[Fall back to a pre-written backup set of test cases]
    G -->|Yes| I[Use the AI's test cases]
    H --> J[Test cases ready for review]
    I --> J
```

---

## 15. How the pieces fit together (very simplified system map)

```mermaid
flowchart LR
    U[You, in the browser] -->|clicks buttons| FE[The Website<br/>Next.js frontend]
    FE -->|asks for things| BE[The Backend<br/>Python server]
    BE -->|reads and saves everything| DB[(The Database<br/>PostgreSQL)]
    BE -->|asks for help writing/judging/fixing| AI[The AI Model<br/>OpenAI]
    BE -->|sends test messages to| AGENT[The Chatbot Being Tested]
    AGENT -->|replies + trace| BE
    BE -->|report data| FE
    FE -->|shows the report| U
```

---

## 16. Quick reference: where each flow starts

| If you want to see... | Look at diagram section |
|---|---|
| The very first choice a user makes | Section 1 |
| Connecting your own chatbot for the first time | Section 2 |
| Picking a chatbot you've tested before | Section 3 |
| How test cases get loaded onto the Review screen | Section 4 |
| Everything you can do on the Review screen | Section 5 |
| What happens when you hit Run (one chatbot) | Section 6 |
| What happens when you hit Run (many chatbots) | Section 7 |
| How parallel runs are kept from overloading the server (the "two bouncers") | Section 8 |
| How to read the final Report | Section 9 |
| What the server does during a Run, step by step | Section 10 |
| How one conversation gets judged pass/fail | Section 11 |
| How the final 0-100 score is calculated | Section 12 |
| What happens the moment you connect a new chatbot | Section 13 |
| How test cases are actually written by AI | Section 14 |
| The whole system, zoomed all the way out | Section 15 |
