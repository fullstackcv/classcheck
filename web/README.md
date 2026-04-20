# ClassCheck Web

Next.js 15 App Router frontend. Talks to the FastAPI backend at
`NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).

## Dev loop

```bash
# First time:
cp .env.local.example .env.local
npm install

# Every time:
npm run dev
```

Open http://localhost:3000.

Make sure the FastAPI backend is also running:

```bash
# in another terminal, at the repo root
classcheck-api
```

## Routes

| Page | URL |
|---|---|
| Classes | `/classes` |
| People | `/people` |
| Enroll | `/enroll` |
| Attendance | `/attendance` |

## Where things live

- `src/app/` — App Router pages, one per route.
- `src/components/` — UI + feature components (sidebar, webcam capture).
- `src/components/ui/` — shadcn-style primitives (button, input, card).
- `src/lib/api.ts` — typed fetch wrapper. Every backend call goes through here.
- `src/lib/utils.ts` — `cn()` Tailwind class merger.
