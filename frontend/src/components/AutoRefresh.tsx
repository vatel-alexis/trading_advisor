"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

// Re-renders the server page on an interval, so marks and order states stay current.
export function AutoRefresh({ seconds }: { seconds: number }) {
  const router = useRouter();
  useEffect(() => {
    const timer = setInterval(() => router.refresh(), seconds * 1000);
    return () => clearInterval(timer);
  }, [router, seconds]);
  return null;
}
