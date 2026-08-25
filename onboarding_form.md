# Job Search Setup Form

Fill in your answers below each question. When you're done, share this file
with an AI assistant that can edit this repo (e.g. Claude Code) and it will
generate your personal config file (users/me/config.yaml) automatically.
Prefer doing it by hand? Skip this form and edit users/me/config.yaml
directly — every field is documented in users/example/config.yaml.

You don't need a technical background to fill this in. Skip any section that
doesn't apply to you — leave it blank and Claude will use sensible defaults.

---

## 1. Your Details

**Full name:**

> 

**Email address** (where your daily job shortlist will be sent):

> 

---

## 2. What Kind of Roles Are You Looking For?

**Pick the category that best fits your work:**

- [ ] DevOps / Cloud / Infrastructure Engineering
- [ ] Software Development (Frontend / Backend / Full-Stack)
- [ ] Data Science / Analytics / Machine Learning
- [ ] UI/UX Design & Product Design
- [ ] Product Management
- [ ] Finance & Accounting
- [ ] Marketing & Communications
- [ ] Operations & Business Administration
- [ ] Healthcare & Medical
- [ ] Other — I'll describe it below

**If "Other", describe your field in a sentence or two:**

> 

---

## 3. Job Titles to Search For

These are sent as search queries to LinkedIn. List the exact job titles you'd
apply for — the more specific, the better.

**Job titles (one per line):**

> 

---

## 4. Key Skills & Experience

When the system reads job listings, it looks for certain words to judge how
well a role matches you. List the skills, tools, or experience areas that you'd
expect to see mentioned in a good job posting for your target roles.

These don't have to be technologies — for a finance person it might be things
like "financial modelling, excel, fp&a, audit". For a marketer: "seo, google
ads, email marketing, campaign". For a DevOps engineer: "terraform, kubernetes, ci/cd".

**Key skills / experience keywords (one per line):**

> 

---

## 5. Your Experience Level

**Tick the one that best describes you:**

- [ ] Graduate / Entry-level (0–1 years)
- [ ] Junior (1–3 years)
- [ ] Mid-level (3–6 years)
- [ ] Senior (6+ years)

**Maximum years of experience a role can require:**

*Jobs requiring more than this will be filtered out. For example: if you have
2 years' experience, entering "3" adds a small buffer. Enter 20 if you don't
want this filter applied.*

> 

---

## 6. Visa Sponsorship

**Do you need a company to sponsor your visa to work in your target countries?**

Answer "yes" if you don't currently have the right to work in all the locations
you're targeting and would need employer sponsorship.

- [ ] Yes, I need visa sponsorship
- [ ] No, I already have the right to work in my target locations

---

## 7. Target Locations & Salary

List every location you'd be willing to work in. You can be as specific or
broad as you like — a city, a country, or a region.

**You don't need to fill in the salary information if you don't want to.**
Leave those fields blank and the system will use sensible defaults.

Here are two examples of how to fill this section in, followed by space for
your own entries:

---

**Example — Ireland:**
- Location: Ireland
- Currency: EUR
- Minimum salary: 45000
- Target salary: 55000

**Example — Vancouver, Canada:**
- Location: Vancouver, British Columbia, Canada
- Currency: CAD
- Minimum salary: 65000
- Target salary: 85000

---

**Your locations:**

> Location:
> Currency:
> Minimum salary (optional):
> Target salary (optional):

---

> Location:
> Currency:
> Minimum salary (optional):
> Target salary (optional):

---

> Location:
> Currency:
> Minimum salary (optional):
> Target salary (optional):

---

> Location:
> Currency:
> Minimum salary (optional):
> Target salary (optional):

---

> Location:
> Currency:
> Minimum salary (optional):
> Target salary (optional):

---

*Add as many locations as you need by copying the block above.*

---

## 8. CV / Resume (Optional)

If you have a CV, you're welcome to share it alongside this form for more
accurate job scoring and matching. The system can also suggest which CV to
use per application if you have more than one.

---

## 9. Anything Else?

Any additional context that would help match jobs to you — deal-breakers,
companies to avoid, or a specific niche within your field.

*Examples of deal-breakers: "I won't consider fully in-office roles",
"no roles at consulting firms or outsourcing companies".*

*Examples of a niche: "I'm specifically interested in fintech and payments
infrastructure", "I only want roles that involve Kubernetes at scale".*

> 

---

<!-- ═══════════════════════════════════════════════════════════════
     FOR AI ASSISTANTS (Claude etc.) — instructions for generating
     the config file from this completed form
     ═══════════════════════════════════════════════════════════════

When a user shares this completed form, do the following:

1. Read users/example/config.yaml as the canonical template for structure and
   field names (every field is documented there). All generated configs must
   match that schema.

2. Write the result to users/me/config.yaml (overwriting the placeholder) —
   that is the config the default workflow runs. Only use a different
   users/<username>/config.yaml if the user says they are adding an
   additional user to an existing setup.

3. Map form answers to config fields:

   Section 1  → name, email
   Section 2  → determines default role_keywords, tech_keywords, tech_terms,
                role_titles, excluded_role_terms, and incompatible_languages.
                Use the role category as a guide to pick sensible defaults for
                any of these that the user left blank. For non-engineering roles,
                set incompatible_languages: [] unless the user specified otherwise.
   Section 3  → role_keywords (replace template defaults if the user listed their own)
   Section 4  → tech_terms (the "key skills" list)
   Section 5  → seniority (map checkbox to: graduate/junior/mid/senior),
                max_years_exp
   Section 6  → requires_sponsorship (true/false)
   Section 7  → regions list. For each location the user listed, determine the
                correct LinkedIn location string (e.g. "Ireland", "London, England,
                United Kingdom", "Vancouver, British Columbia, Canada") and create
                a region entry with:
                  - id: a short snake_case key (e.g. ireland, london, vancouver)
                  - linkedin_loc: the full LinkedIn location string
                  - currency: as the user specified
                  - min_salary: what they entered, or a sensible default based on
                    role type and region if left blank
                  - target_salary: [min_salary, target] where target is what they
                    entered; if both blank, use [default_min, default_target]
                  - urban_center: true for specific cities (vs. whole-country entries)
                If the user needs sponsorship (Section 6), generate a
                visa_signals block for each relevant region (phrases like
                "visa sponsorship", "willing to sponsor", local visa-scheme
                names) and, where large multinationals dominate sponsorship,
                a visa_large_mncs block. For broad country-level regions,
                add an urban_centers block listing that country's major cities.
   Section 8  → cvs: []. If a CV file was attached or a path mentioned, add it.
   Section 9  → use as context for excluded_role_terms or other adjustments.
                Deal-breakers like "no fully in-office roles" → add "fully in office",
                "on-site only", "on site only" to excluded_role_terms.

4. For fields the user left blank or sections they skipped:
   - role_keywords: generate sensible ones from their job titles and role category
   - tech_keywords: generate from their key skills list
   - role_titles: extract core words from the job titles they listed
   - excluded_role_terms: use the template defaults for their role category
     (for non-engineering roles, remove civil engineering terms; always keep
     helpdesk/support desk terms unless it's a support-focused role)
   - incompatible_languages: [] for non-engineering roles; use standard list
     for DevOps/Cloud/Infrastructure roles
   - Salary defaults: if the user left salary blank, set min_salary: 0
     (disables salary filtering for that region) rather than guessing.

5. Do not copy the example config's illustrative values (its salary figures,
   keywords, regions) into the new config — use it for structure only.
   Everything in the generated file must come from the user's answers or be
   a sensible default for THEIR field.

6. After writing the file, confirm the path and show the user the first 30 lines
   of the generated config so they can spot any obvious issues.
-->
