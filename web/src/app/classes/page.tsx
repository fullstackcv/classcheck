"use client";

import { useEffect, useState } from "react";
import { Camera, Loader2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import {
  type ClassOut,
  type ClassDetailOut,
  type PersonOut,
  type CaptureResult,
  listClasses,
  createClass,
  updateClass,
  deleteClass,
  captureNow,
  getClass,
  listPeople,
} from "@/lib/api";

export default function ClassesPage() {
  const [classes, setClasses] = useState<ClassOut[] | null>(null);
  const [people, setPeople] = useState<PersonOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const [c, p] = await Promise.all([listClasses(), listPeople()]);
      setClasses(c);
      setPeople(p);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <div className="p-8 max-w-4xl space-y-6">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">Classes</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Each class has a name, a time, a teacher, and a roster of students.
        </p>
      </header>

      {error && (
        <div className="text-sm text-destructive border border-destructive/30 rounded-md p-3 bg-destructive/5">
          {error}
        </div>
      )}

      <NewClassForm people={people} onCreated={refresh} />

      {classes === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : classes.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No classes yet — create one above to get started.
        </p>
      ) : (
        <div className="space-y-4">
          {classes.map((c) => (
            <ClassCard
              key={c.id}
              summary={c}
              people={people}
              onChanged={refresh}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function NewClassForm({
  people,
  onCreated,
}: {
  people: PersonOut[];
  onCreated: () => void;
}) {
  const [label, setLabel] = useState("");
  const [time, setTime] = useState("09:00");
  const [teacherId, setTeacherId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const teachers = people.filter((p) => p.role === "teacher");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await createClass({
        label,
        time_of_day: time,
        teacher_id: teacherId === "" ? null : Number(teacherId),
      });
      setLabel("");
      setTime("09:00");
      setTeacherId("");
      onCreated();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Plus className="h-4 w-4" /> New class
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid grid-cols-[1fr_110px_1fr_auto] gap-3 items-end">
          <div className="space-y-1.5">
            <Label htmlFor="label">Name</Label>
            <Input
              id="label"
              placeholder="e.g. Math-10A"
              value={label}
              required
              onChange={(e) => setLabel(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="time">Time</Label>
            <Input
              id="time"
              type="time"
              value={time}
              required
              onChange={(e) => setTime(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="teacher">Teacher</Label>
            <select
              id="teacher"
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={teacherId}
              onChange={(e) => setTeacherId(e.target.value)}
            >
              <option value="">— none —</option>
              {teachers.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>
          <Button type="submit" disabled={busy || !label}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create"}
          </Button>
        </form>
        {err && <p className="mt-3 text-sm text-destructive">{err}</p>}
      </CardContent>
    </Card>
  );
}

function ClassCard({
  summary,
  people,
  onChanged,
}: {
  summary: ClassOut;
  people: PersonOut[];
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<ClassDetailOut | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [captureMsg, setCaptureMsg] = useState<string | null>(null);

  async function loadDetail() {
    try {
      setDetail(await getClass(summary.id));
    } catch (e) {
      setCaptureMsg(String(e));
    }
  }

  async function runCapture() {
    setCapturing(true);
    setCaptureMsg(null);
    try {
      const r: CaptureResult = await captureNow(summary.id, true);
      if (r.ok && r.snapshot_id !== null) {
        setCaptureMsg(`Captured snapshot #${r.snapshot_id}. Check the Attendance tab.`);
      } else {
        setCaptureMsg("Capture didn't produce a snapshot — check server logs.");
      }
    } catch (e) {
      setCaptureMsg(String(e));
    } finally {
      setCapturing(false);
    }
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>{summary.label}</CardTitle>
            <p className="text-sm text-muted-foreground mt-0.5">
              <span className="font-medium">Teacher:</span>{" "}
              {summary.teacher_name ?? "—"}
              {"  ·  "}
              <span className="font-medium">{summary.student_count}</span>{" "}
              student{summary.student_count !== 1 && "s"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-semibold text-blue-600 tabular-nums">
              {summary.time_of_day}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={runCapture}
              disabled={capturing}
              title="Trigger a sampling burst right now"
            >
              {capturing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Camera className="h-4 w-4" />
              )}
              Capture now
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {captureMsg && (
          <p className="text-sm text-muted-foreground mb-3">{captureMsg}</p>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={async () => {
            const next = !expanded;
            setExpanded(next);
            if (next && !detail) await loadDetail();
          }}
        >
          {expanded ? "Close" : "Edit"}
        </Button>
        {expanded && detail && (
          <ClassEditor
            detail={detail}
            people={people}
            onSaved={async () => {
              await loadDetail();
              onChanged();
            }}
            onDeleted={onChanged}
          />
        )}
      </CardContent>
    </Card>
  );
}

function ClassEditor({
  detail,
  people,
  onSaved,
  onDeleted,
}: {
  detail: ClassDetailOut;
  people: PersonOut[];
  onSaved: () => void | Promise<void>;
  onDeleted: () => void | Promise<void>;
}) {
  const [label, setLabel] = useState(detail.label);
  const [time, setTime] = useState(detail.time_of_day);
  const [teacherId, setTeacherId] = useState<string>(
    detail.teacher_id === null ? "" : String(detail.teacher_id)
  );
  const [studentIds, setStudentIds] = useState<number[]>(detail.student_ids);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const teachers = people.filter((p) => p.role === "teacher");
  const students = people.filter((p) => p.role === "student");

  function toggleStudent(id: number) {
    setStudentIds((s) =>
      s.includes(id) ? s.filter((x) => x !== id) : [...s, id]
    );
  }

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await updateClass(detail.id, {
        label,
        time_of_day: time,
        teacher_id: teacherId === "" ? null : Number(teacherId),
        student_ids: studentIds,
      });
      await onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirm(`Delete class "${detail.label}"?`)) return;
    setBusy(true);
    try {
      await deleteClass(detail.id);
      await onDeleted();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 space-y-4 border-t pt-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label>Name</Label>
          <Input value={label} onChange={(e) => setLabel(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label>Time</Label>
          <Input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
          />
        </div>
        <div className="col-span-2 space-y-1.5">
          <Label>Teacher</Label>
          <select
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
            value={teacherId}
            onChange={(e) => setTeacherId(e.target.value)}
          >
            <option value="">— none —</option>
            {teachers.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <Label className="mb-2 inline-block">Students</Label>
        {students.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No students enrolled in the system yet. Go to the Enroll tab.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {students.map((s) => (
              <label
                key={s.id}
                className="flex items-center gap-2 text-sm p-2 rounded hover:bg-accent/50 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={studentIds.includes(s.id)}
                  onChange={() => toggleStudent(s.id)}
                />
                {s.name}
              </label>
            ))}
          </div>
        )}
      </div>

      {err && <p className="text-sm text-destructive">{err}</p>}

      <div className="flex gap-2">
        <Button onClick={save} disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save changes"}
        </Button>
        <Button variant="destructive" onClick={remove} disabled={busy}>
          <Trash2 className="h-4 w-4" /> Delete
        </Button>
      </div>
    </div>
  );
}
