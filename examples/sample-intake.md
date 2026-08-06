Fwd: notes from the Tuesday sync + the thing Priya raised

---

Ok so pulling together what we have so far. This is rough.

Background: we run support for about 40 mid-market SaaS companies (our
customers are the companies, the actual end users are their support agents).
Average team is 6-12 agents. They live in Zendesk all day.

The problem Priya keeps hearing on calls: agents spend most of their time
writing the same five replies over and over, but every reply still needs
context from the customer's account, plan tier, recent tickets, whether
they've churned before. So canned responses don't actually help. Agents
copy-paste a template and then spend 4-5 minutes hunting through three tabs
to personalize it.

She pulled some numbers from two accounts:
- median first-response time: 3h 20m
- ~60% of tickets are one of about 8 recurring categories
- agents handle ~35 tickets/day, say 6 min each on the "easy" ones

Idea we've been circling: a drafting assistant inside Zendesk that reads the
ticket + pulls the account context automatically and writes a first draft the
agent edits and sends. Explicitly NOT auto-send. Agents were burned by a
chatbot vendor two years ago and there's real skepticism.

Things we don't know / arguments we keep having:
- Does it draft for all ticket types or only the recurring 8? Marcus wants
  everything, I think that's how you get slop.
- What happens on a ticket about billing where being wrong is expensive
- Whether we can even get the account context out of their systems, some are
  on Salesforce, some on HubSpot, two have a homegrown thing
- Pricing. Per seat? Per draft? Nobody has strong feelings yet

Competition: Zendesk ships their own AI thing now, which is the obvious
"why not just use that" objection. Our angle is supposed to be that we
already have the integration work done for these 40 accounts and Zendesk's
version doesn't reach into their CRM. Not sure that's enough.

Constraints:
- 2 engineers, one is part-time
- want something in front of a design partner in 8 weeks
- one of our customers is in healthcare, so there's a HIPAA conversation
  coming that nobody has had yet
- legal will want to know where the data goes

Priya's line from the call, which I think is the actual pitch: "I don't want
it to answer the ticket. I want it to have already done the reading."

Success looks like... honestly we haven't defined this. Something like agents
choosing to use it without being told to. If it saves 2 min a ticket that's
roughly an hour a day per agent.
