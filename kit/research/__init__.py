"""The research pipeline: what the creators on a watch list posted, scored against their own normal.

A run gathers recent videos (Instagram and TikTok through Apify when there is a key, YouTube through
yt-dlp), the links and ideas in me/ideas.md, and optionally the creator's own results. It scores every
video, writes research/runs/<date>/items.json and report.html, and leaves the ideas to the skill. An offline
run on made-up sample videos writes to research/samples/<date>/ instead, which no idea picker reads.

    uv run python -m kit.research run [--offline] [--no-apify] [--no-transcribe]
    uv run python -m kit.research report [--date YYYY-MM-DD]
    uv run python -m kit.research hooks <n> [--save] [--date YYYY-MM-DD]
"""
