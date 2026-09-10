# Third-party components

The directories below are adapted from **K-Dense-AI/scientific-agent-skills** release `v2.66.0`, commit `1e5eeffbdad3749125afe7ab48a39694e27f181c`:

- `.agents/skills/scientific-brainstorming`
- `.agents/skills/hypothesis-generation`
- `.agents/skills/scientific-critical-thinking`
- `.agents/skills/statistical-analysis`
- `.agents/skills/scientific-writing`
- `.agents/skills/paper-lookup`
- `.agents/skills/experimental-design`
- `.agents/skills/peer-review`

Upstream: <https://github.com/K-Dense-AI/scientific-agent-skills>

License: MIT. The upstream `LICENSE` is included as `LICENSE-K-DENSE.md`.

Adaptation: informational `compatibility` frontmatter fields were removed where present so all eight imports pass the stricter portable Skill validator. The same requirements remain in each Skill body and in `skills.lock.json`; instructions, scripts, references and assets are otherwise unchanged from the official v2.66.0 archive.

These Skills supply phase-specific scientific methods. Their presence does not make experimental claims valid by itself; this project adds state transitions, artifact verification, approvals and stopping rules around them.
