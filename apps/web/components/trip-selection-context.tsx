"use client";

import { createContext, useContext } from "react";
import type { Trip } from "../lib/api";

export type TripSelectionContextValue = {
  trips: Trip[];
  selectedTripId: string;
  selectTrip: (tripId: string) => void;
};

const TripSelectionContext = createContext<TripSelectionContextValue | null>(null);

export const TripSelectionProvider = TripSelectionContext.Provider;

export function tripOptionLabel(trip: Trip, trips: Trip[]) {
  const matches = trips.filter((item) => item.destination.trim().toLocaleLowerCase() === trip.destination.trim().toLocaleLowerCase()
    && item.start_date === trip.start_date && item.end_date === trip.end_date);
  const base = `${trip.destination} · ${trip.start_date} 至 ${trip.end_date}`;
  if (matches.length < 2) return base;
  const createdAt = new Date(trip.created_at);
  const timestamp = Number.isNaN(createdAt.getTime()) ? "创建时间未知" : createdAt.toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  return `${base} · 创建于 ${timestamp} · ${trip.id.slice(0, 4)}`;
}

export function useTripSelection() {
  const context = useContext(TripSelectionContext);
  if (!context) throw new Error("useTripSelection must be used within WorkspaceShell");
  return context;
}
