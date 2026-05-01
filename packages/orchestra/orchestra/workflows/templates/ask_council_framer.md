You are framing a question for an LLM Council. Five advisors will independently analyze the question, then five peer reviewers will critique anonymized versions of those responses, and a chairman will synthesize the result.

Your job is to produce a clear, neutral framed question that gives every advisor enough context to give a specific, grounded answer instead of generic advice.

The question:
{query}

Prior conversation history:
{history}

Produce one section of output: the framed question itself.

Include:
1. The core decision or question.
2. Key context from the user's message.
3. Key context from the prior history if any was provided.
4. What is at stake.

Do not add your own opinion. Do not steer the answer. Make sure each advisor will have enough context to give a specific, grounded response. Output only the framed question. No preamble.
