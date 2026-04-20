import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind class merger. `cn("px-2", cond && "bg-red-500")`. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
