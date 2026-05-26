# SuperSplat Training Backend

FastAPI service exposing the photos/video → PLY pipeline as HTTP endpoints.
Lives at `F:\SUPERSPLAT\server\` and runs on `http://127.0.0.1:8000`.

## Architecture

```
Browser (SuperSplat :3000)         FastAPI (:8000)
    │                                   │
    │  File → Train New Splat...        │
    │ ─────────────────────────────────►│
    │  POST /jobs (multipart)            │
    │ ◄──── { id: "abc123", ... }       │
    │                                    │
    │  EventSource /jobs/abc123/stream   │
    │ ◄──── status updates (SSE)        │
    │                                    │  ┌── ffmpeg
    │                                    │──┤── ns-process-data (COLMAP)
    │                                    │  ├── ns-train splatfacto
    │                                    │  └── ns-export gaussian-splat
    │                                    │
    │  GET /jobs/abc123/ply              │
    │ ◄──── splat.ply (binary)          │
    │                                    │
    │  scene loads PLY in the editor     │
```

Single-job queue: one training runs at a time. Submitting while a job runs
adds the new job to the queue.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | API info |
| POST | `/jobs` | Upload video or photos, returns job state |
| GET | `/jobs` | List all jobs |
| GET | `/jobs/{id}` | Current status JSON |
| GET | `/jobs/{id}/stream` | SSE stream of status updates |
| GET | `/jobs/{id}/log` | Plain-text training log |
| GET | `/jobs/{id}/ply` | Download finished PLY |
| DELETE | `/jobs/{id}` | Remove job and its files |

### POST /jobs (multipart/form-data)

| Field | Type | Notes |
|---|---|---|
| `files` | file(s) | One video OR multiple photos |
| `matcher` | string | `sequential` (default) or `exhaustive` |
| `max_iters` | int | Training iterations (default 30000) |
| `name` | string | Optional friendly name |

### Status JSON

```json
{
  "id": "abc123",
  "name": "myclip.mp4",
  "created_at": 1748185200,
  "stage": "training",
  "stage_progress": 0.42,
  "overall": 0.6,
  "message": "Training splatfacto (30000 iterations)",
  "error": null,
  "upload_kind": "video",
  "num_files": 1,
  "matcher": "sequential",
  "max_iters": 30000,
  "has_ply": false
}
```

Stages: `queued → preparing → extracting → colmap → training → exporting → done`
(or `failed`).

## Running

```powershell
cd F:\SUPERSPLAT\server
.\run.ps1
# or
& "F:\SUPERSPLAT\venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Hot reload during development:

```powershell
& "F:\SUPERSPLAT\venv\Scripts\python.exe" -m uvicorn main:app --reload
```

## Storage layout

```
server/jobs/<job-id>/
├── upload/      # original files dropped by the user
├── images/      # extracted frames (or copied photos)
├── workspace/   # COLMAP output (transforms.json, sparse/, ...)
├── outputs/     # nerfstudio training checkpoints
├── exports/     # ns-export output (intermediate PLY)
├── splat.ply    # final PLY served to clients
├── log.txt      # combined stdout+stderr of all subprocesses
└── status.json  # last-known job state
```

Each job consumes 100 MB - 5 GB depending on dataset size. Use
`DELETE /jobs/{id}` or just `rm -r server/jobs/<id>` to reclaim space.

## Testing manually with curl

```powershell
# Submit
curl.exe -X POST http://127.0.0.1:8000/jobs `
    -F "files=@C:\path\to\myclip.mp4" `
    -F "matcher=sequential" -F "max_iters=15000"

# Watch
curl.exe -N http://127.0.0.1:8000/jobs/<id>/stream

# Download PLY
curl.exe -o out.ply http://127.0.0.1:8000/jobs/<id>/ply
```

## CORS

Origin `http://localhost:3000` and `http://127.0.0.1:3000` are allowed.
Add more origins in `main.py` `allow_origins=[...]` if hosting SuperSplat
on a different port.
