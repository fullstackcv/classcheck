"use client";

import { useCallback, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EnrollWebcam } from "@/components/enroll-webcam";
import { enroll } from "@/lib/api";

export default function EnrollPage() {
  const [name, setName] = useState("");
  const [role, setRole] = useState("student");
  const [email, setEmail] = useState("");
  const [photos, setPhotos] = useState<Blob[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    id: number;
    name: string;
    role: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onPhotos = useCallback((p: Blob[]) => setPhotos(p), []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await enroll({
        name,
        role,
        email: email || undefined,
        photos,
      });
      setResult(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = name.trim() !== "" && photos.length > 0 && !busy;

  return (
    <div className="p-8 max-w-3xl space-y-6">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">Enroll</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Take 3–5 photos of a new person. After enrolling, assign their classes
          from the <strong>People</strong> tab.
        </p>
      </header>

      <form onSubmit={submit} className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Person</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="n">Name</Label>
                <Input
                  id="n"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Alice Nair"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="r">Role</Label>
                <select
                  id="r"
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
                <Label htmlFor="e">Email (optional — for reports)</Label>
                <Input
                  id="e"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@school.edu"
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Camera</CardTitle>
          </CardHeader>
          <CardContent>
            <EnrollWebcam onPhotos={onPhotos} />
          </CardContent>
        </Card>

        {error && (
          <div className="text-sm text-destructive border border-destructive/30 rounded-md p-3 bg-destructive/5">
            {error}
          </div>
        )}

        {result && (
          <div className="text-sm border border-green-600/30 bg-green-50 rounded-md p-3">
            ✅ Enrolled <strong>{result.name}</strong> as{" "}
            <strong>{result.role}</strong> (id #{result.id}).{" "}
            Open the <strong>People</strong> tab to assign their classes.
          </div>
        )}

        <Button type="submit" disabled={!canSubmit} size="lg">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {busy ? "Enrolling — loading face recognition pipeline…" : "Enroll"}
        </Button>
      </form>
    </div>
  );
}
