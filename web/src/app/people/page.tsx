"use client";

import { useEffect, useState } from "react";
import { Loader2, Trash2, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import {
  type ClassOut,
  type PersonOut,
  type PersonDetailOut,
  listClasses,
  listPeople,
  getPerson,
  updatePerson,
  deletePerson,
  setPersonClasses,
} from "@/lib/api";

export default function PeoplePage() {
  const [people, setPeople] = useState<PersonOut[] | null>(null);
  const [classes, setClasses] = useState<ClassOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const [p, c] = await Promise.all([listPeople(), listClasses()]);
      setPeople(p);
      setClasses(c);
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
        <h2 className="text-2xl font-semibold tracking-tight">People</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Everyone registered in the system. Use the Enroll tab to add someone new.
        </p>
      </header>

      {error && (
        <div className="text-sm text-destructive border border-destructive/30 rounded-md p-3 bg-destructive/5">
          {error}
        </div>
      )}

      {people === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : people.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No people yet. Go to the Enroll tab to register someone.
        </p>
      ) : (
        <div className="space-y-4">
          {people.map((p) => (
            <PersonCard
              key={p.id}
              summary={p}
              classes={classes}
              onChanged={refresh}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function PersonCard({
  summary,
  classes,
  onChanged,
}: {
  summary: PersonOut;
  classes: ClassOut[];
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<PersonDetailOut | null>(null);

  const roleEmoji = summary.role === "teacher" ? "👩‍🏫"
    : summary.role === "admin" ? "⚙️"
    : "🎓";

  async function loadDetail() {
    setDetail(await getPerson(summary.id));
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2">
              <span>{roleEmoji}</span>
              {summary.name}
            </CardTitle>
            <p className="text-sm text-muted-foreground mt-0.5">
              <span className="font-medium capitalize">{summary.role}</span>
              {summary.email && <>  ·  📧 {summary.email}</>}
              {"  ·  "}
              {summary.role === "teacher"
                ? `Teaches ${summary.class_count} class${summary.class_count !== 1 ? "es" : ""}`
                : summary.role === "student"
                ? `In ${summary.class_count} class${summary.class_count !== 1 ? "es" : ""}`
                : "Admin"}
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={async () => {
              const next = !expanded;
              setExpanded(next);
              if (next && !detail) await loadDetail();
            }}
          >
            {expanded ? "Close" : "Details"}
          </Button>
        </div>
      </CardHeader>
      {expanded && detail && (
        <CardContent className="pt-0">
          <PersonEditor
            detail={detail}
            classes={classes}
            onSaved={async () => {
              await loadDetail();
              onChanged();
            }}
            onDeleted={onChanged}
          />
        </CardContent>
      )}
    </Card>
  );
}

function PersonEditor({
  detail,
  classes,
  onSaved,
  onDeleted,
}: {
  detail: PersonDetailOut;
  classes: ClassOut[];
  onSaved: () => void | Promise<void>;
  onDeleted: () => void | Promise<void>;
}) {
  const [name, setName] = useState(detail.name);
  const [role, setRole] = useState(detail.role);
  const [email, setEmail] = useState(detail.email ?? "");
  const [classIds, setClassIds] = useState<number[]>(
    role === "teacher" ? detail.teaches_ids : detail.enrolled_ids
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function toggleClass(id: number) {
    setClassIds((s) =>
      s.includes(id) ? s.filter((x) => x !== id) : [...s, id]
    );
  }

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await updatePerson(detail.id, {
        name,
        role,
        email: email || null,
      });
      if (role !== "admin") await setPersonClasses(detail.id, classIds);
      await onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirm(`Delete ${detail.name}? This cannot be undone.`)) return;
    setBusy(true);
    try {
      await deletePerson(detail.id);
      await onDeleted();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border-t pt-4 space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label>Name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label>Role</Label>
          <select
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            <option value="student">Student</option>
            <option value="teacher">Teacher</option>
            <option value="admin">Admin</option>
          </select>
        </div>
        <div className="col-span-2 space-y-1.5">
          <Label>Email (optional — for attendance reports)</Label>
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
      </div>

      {role !== "admin" && (
        <div>
          <Label className="mb-2 inline-block">
            {role === "teacher" ? "Classes they teach" : "Classes they're in"}
          </Label>
          {classes.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No classes set up yet.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {classes.map((c) => (
                <label
                  key={c.id}
                  className="flex items-center gap-2 text-sm p-2 rounded hover:bg-accent/50 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={classIds.includes(c.id)}
                    onChange={() => toggleClass(c.id)}
                  />
                  {c.label} @ {c.time_of_day}
                </label>
              ))}
            </div>
          )}
        </div>
      )}

      {detail.recent_observations.length > 0 && (
        <div>
          <Label className="mb-2 inline-block">Recent attendance</Label>
          <div className="border rounded-md overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left">
                <tr>
                  <th className="px-3 py-2 font-medium">Date</th>
                  <th className="px-3 py-2 font-medium">Time</th>
                  <th className="px-3 py-2 font-medium">Class</th>
                  <th className="px-3 py-2 font-medium">Seen</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {detail.recent_observations.map((o, i) => (
                  <tr key={i} className="border-t">
                    <td className="px-3 py-2">{o.date}</td>
                    <td className="px-3 py-2">{o.time}</td>
                    <td className="px-3 py-2">{o.class}</td>
                    <td className="px-3 py-2">{o.seen}</td>
                    <td className="px-3 py-2">
                      <span
                        className={
                          o.status === "Present"
                            ? "text-green-600 font-medium"
                            : "text-muted-foreground"
                        }
                      >
                        {o.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {err && <p className="text-sm text-destructive">{err}</p>}

      <div className="flex gap-2">
        <Button onClick={save} disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
        </Button>
        <Button variant="destructive" onClick={remove} disabled={busy}>
          <Trash2 className="h-4 w-4" /> Delete
        </Button>
      </div>
    </div>
  );
}
