"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * Browser-native webcam capture for enrollment.
 *
 * - getUserMedia({ video }) on mount → live preview in a <video>.
 * - Click "Take photo" → snap current frame to a hidden <canvas> and push
 *   the blob into the captured list.
 * - Parent receives the list of blobs and uploads them to the API.
 *
 * Replaces the `streamlit-webrtc` path from the v1 Streamlit dashboard.
 * Works on mobile Safari because it's vanilla getUserMedia, not WebRTC
 * peer-to-peer.
 */
export function EnrollWebcam({
  onPhotos,
}: {
  onPhotos: (blobs: Blob[]) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [photos, setPhotos] = useState<Blob[]>([]);
  const [previews, setPreviews] = useState<string[]>([]);

  // Start the camera once on mount. Cleanup stops all tracks so the
  // camera LED turns off.
  useEffect(() => {
    let mounted = true;
    let activeStream: MediaStream | null = null;

    (async () => {
      try {
        const s = await navigator.mediaDevices.getUserMedia({
          video: { width: 640, height: 480, facingMode: "user" },
          audio: false,
        });
        activeStream = s;
        if (mounted && videoRef.current) {
          videoRef.current.srcObject = s;
        }
      } catch (e) {
        setStreamError(
          e instanceof Error ? e.message : "Could not access the camera."
        );
      }
    })();

    return () => {
      mounted = false;
      activeStream?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  // Keep the parent in sync when our internal list changes.
  useEffect(() => {
    onPhotos(photos);
  }, [photos, onPhotos]);

  const take = useCallback(async () => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || video.readyState < 2) return;

    const w = video.videoWidth || 640;
    const h = video.videoHeight || 480;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, w, h);

    const blob = await new Promise<Blob | null>((res) =>
      canvas.toBlob(res, "image/jpeg", 0.92)
    );
    if (!blob) return;

    const url = URL.createObjectURL(blob);
    setPhotos((ps) => [...ps, blob]);
    setPreviews((ps) => [...ps, url]);
  }, []);

  const reset = () => {
    previews.forEach((u) => URL.revokeObjectURL(u));
    setPreviews([]);
    setPhotos([]);
  };

  const removeOne = (i: number) => {
    URL.revokeObjectURL(previews[i]);
    setPreviews((ps) => ps.filter((_, idx) => idx !== i));
    setPhotos((ps) => ps.filter((_, idx) => idx !== i));
  };

  return (
    <div className="space-y-4">
      {streamError ? (
        <div className="text-sm text-destructive border border-destructive/30 rounded-md p-3 bg-destructive/5">
          Camera unavailable: {streamError}
          <p className="text-xs mt-2 text-muted-foreground">
            Check that your browser has permission to access the camera.
            On Chrome/Safari, click the camera icon in the address bar.
          </p>
        </div>
      ) : (
        <div className="relative rounded-md overflow-hidden border bg-black">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="w-full aspect-[4/3] object-cover"
          />
          <canvas ref={canvasRef} className="hidden" />
        </div>
      )}

      <div className="flex items-center gap-2">
        <Button onClick={take} disabled={!!streamError}>
          <Camera className="h-4 w-4" /> Take photo
        </Button>
        {photos.length > 0 && (
          <Button variant="outline" onClick={reset}>
            <RefreshCw className="h-4 w-4" /> Reset
          </Button>
        )}
        <span className="text-sm text-muted-foreground ml-2">
          {photos.length} photo{photos.length !== 1 && "s"} — aim for 3–5 from
          slightly different angles.
        </span>
      </div>

      {previews.length > 0 && (
        <div className="grid grid-cols-5 gap-2">
          {previews.map((url, i) => (
            <div key={url} className="relative group">
              <img
                src={url}
                alt={`photo ${i + 1}`}
                className="rounded border aspect-square object-cover w-full"
              />
              <button
                type="button"
                onClick={() => removeOne(i)}
                className="absolute top-1 right-1 bg-black/60 text-white rounded-full w-5 h-5 flex items-center justify-center opacity-0 group-hover:opacity-100 transition"
                aria-label="Remove"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
