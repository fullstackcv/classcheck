"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import {
  type ClassOut,
  type SnapshotSummary,
  type SnapshotDetail,
  listClasses,
  listSnapshots,
  getSnapshot,
  thumbnailUrl,
  rollCsvUrl,
  teachersCsvUrl,
} from "@/lib/api";

export default function AttendancePage() {
  const [classes, setClasses] = useState<ClassOut[]>([]);
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [classId, setClassId] = useState<string>("");
  const [snaps, setSnaps] = useState<SnapshotSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listClasses().then(setClasses).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    setSnaps(null);
    setError(null);
    listSnapshots({
      date,
      class_id: classId === "" ? undefined : Number(classId),
    })
      .then(setSnaps)
      .catch((e) => setError(String(e)));
  }, [date, classId]);

  return (
    <div className="p-8 max-w-4xl space-y-6">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">Attendance</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Filter by date and class. Open a snapshot to see who was recognised.
        </p>
      </header>

      <Card>
        <CardContent className="pt-6">
          <div className="flex gap-3 items-end">
            <div className="space-y-1.5">
              <Label>Date</Label>
              <Input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>
            <div className="space-y-1.5 flex-1">
              <Label>Class</Label>
              <select
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={classId}
                onChange={(e) => setClassId(e.target.value)}
              >
                <option value="">All classes</option>
                {classes.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label} @ {c.time_of_day}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="text-sm text-destructive border border-destructive/30 rounded-md p-3 bg-destructive/5">
          {error}
        </div>
      )}

      {snaps === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : snaps.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No captures for that filter.
        </p>
      ) : (
        <div className="space-y-4">
          {snaps.map((s) => (
            <SnapshotCard key={s.id} summary={s} />
          ))}
        </div>
      )}
    </div>
  );
}

function SnapshotCard({ summary }: { summary: SnapshotSummary }) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<SnapshotDetail | null>(null);

  const statusMark =
    summary.present_count === summary.total_count && summary.total_count > 0
      ? "✅"
      : "📷";

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-4">
          <CardTitle className="text-base">
            {statusMark}{" "}
            <span className="font-semibold">{summary.class_label}</span> @{" "}
            <span className="tabular-nums">{summary.scheduled_time}</span>{" "}
            <span className="text-muted-foreground font-normal">
              — {summary.present_count}/{summary.total_count} present
            </span>
          </CardTitle>
          <Button
            variant="ghost"
            size="sm"
            onClick={async () => {
              const next = !expanded;
              setExpanded(next);
              if (next && !detail) {
                setDetail(await getSnapshot(summary.id));
              }
            }}
          >
            {expanded ? "Close" : "Open"}
          </Button>
        </div>
      </CardHeader>
      {expanded && detail && (
        <CardContent>
          <SnapshotDetailView detail={detail} />
        </CardContent>
      )}
    </Card>
  );
}

function SnapshotDetailView({ detail }: { detail: SnapshotDetail }) {
  const students = detail.observations.filter((o) => o.role === "student");
  const teachers = detail.observations.filter((o) => o.role === "teacher");

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        Captured at {detail.actual_time.slice(11, 19)} · {detail.n_frames} frames
      </p>

      {detail.thumbnail_url && (
        <img
          src={thumbnailUrl(detail.id)}
          alt="best recognition frame"
          className="rounded-md border w-full max-w-xl"
        />
      )}

      {students.length > 0 && <ObsTable title="Students" rows={students} />}
      {teachers.length > 0 && <ObsTable title="Teachers" rows={teachers} />}

      <div className="flex gap-2 pt-2">
        <a
          href={rollCsvUrl(detail.id)}
          download
          className="inline-flex items-center h-9 px-4 rounded-md border text-sm hover:bg-accent"
        >
          Student roll (CSV)
        </a>
        <a
          href={teachersCsvUrl(detail.id)}
          download
          className="inline-flex items-center h-9 px-4 rounded-md border text-sm hover:bg-accent"
        >
          Teacher attendance (CSV)
        </a>
      </div>
    </div>
  );
}

function ObsTable({
  title,
  rows,
}: {
  title: string;
  rows: SnapshotDetail["observations"];
}) {
  return (
    <div>
      <p className="text-sm font-medium mb-2">{title}</p>
      <div className="border rounded-md overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-left">
            <tr>
              <th className="px-3 py-2 font-medium">Name</th>
              <th className="px-3 py-2 font-medium">Seen</th>
              <th className="px-3 py-2 font-medium">Score</th>
              <th className="px-3 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.person_id} className="border-t">
                <td className="px-3 py-2">{o.name}</td>
                <td className="px-3 py-2 tabular-nums">
                  {o.frames_seen}/{o.total_frames}
                </td>
                <td className="px-3 py-2 tabular-nums">
                  {o.avg_score.toFixed(2)}
                </td>
                <td className="px-3 py-2">
                  <span
                    className={
                      o.is_present
                        ? "text-green-600 font-medium"
                        : "text-muted-foreground"
                    }
                  >
                    {o.is_present ? "Present" : "Absent"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
