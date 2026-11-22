// Progressive Web App helpers: service worker, offline state, install prompt, device detection.
import { useEffect, useState } from "react";

type Listener = (offline: boolean) => void;
let offline = typeof navigator !== "undefined" ? !navigator.onLine : false;
const listeners = new Set<Listener>();

export function setOffline(v: boolean) {
  if (v === offline) return;
  offline = v;
  listeners.forEach((l) => l(v));
}

export function useOffline(): boolean {
  const [v, setV] = useState(offline);
  useEffect(() => {
    listeners.add(setV);
    const on = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      listeners.delete(setV);
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);
  return v;
}

/** Service workers need a secure context: https, or http://localhost / 127.0.0.1. */
export function registerServiceWorker(): void {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      /* app still works without offline support */
    });
  });
}

export async function clearApiCache(): Promise<void> {
  const reg = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration() : undefined;
  reg?.active?.postMessage("clear-api-cache");
  if ("caches" in window) {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.endsWith("-api")).map((k) => caches.delete(k)));
  }
}

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: string }>;
}
let deferredPrompt: BeforeInstallPromptEvent | null = null;
const installListeners = new Set<(v: boolean) => void>();
if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e as BeforeInstallPromptEvent;
    installListeners.forEach((l) => l(true));
  });
}

export function useInstallPrompt(): { canInstall: boolean; install: () => Promise<void> } {
  const [canInstall, setCanInstall] = useState(deferredPrompt !== null);
  useEffect(() => {
    installListeners.add(setCanInstall);
    return () => {
      installListeners.delete(setCanInstall);
    };
  }, []);
  return {
    canInstall,
    install: async () => {
      if (!deferredPrompt) return;
      await deferredPrompt.prompt();
      deferredPrompt = null;
      setCanInstall(false);
    },
  };
}

export function isStandalone(): boolean {
  return window.matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

export function isIOS(ua: string = navigator.userAgent): boolean {
  return /iPhone|iPad|iPod/.test(ua) || (ua.includes("Macintosh") && "ontouchend" in document);
}

export function guessDeviceName(ua: string): string {
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) {
    const m = ua.match(/Android [\d.]+; ([^;)]+?)(?: Build|\))/);
    return m ? m[1].trim() : "Android phone";
  }
  return "Phone";
}
