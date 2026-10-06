"use client";

import { KeyRound } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getOwnKey, setOwnKey } from "@/lib/own-key";

export function SettingsDialog() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState(false);

  function onOpenChange(next: boolean) {
    if (next) {
      const current = getOwnKey();
      setDraft(current ?? "");
      setSaved(Boolean(current));
    }
    setOpen(next);
  }

  function save() {
    setOwnKey(draft.trim() || null);
    setOpen(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Settings: use your own Gemini key">
          <KeyRound />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Use your own Gemini key</DialogTitle>
          <DialogDescription>
            Optional. Grepwise shares one free Gemini quota between everyone; with your own key,
            your questions use your quota instead and skip the site-wide daily limit.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <Label htmlFor="gemini-key">Gemini API key</Label>
          <Input
            id="gemini-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="AIza…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <p className="text-muted-foreground text-xs leading-relaxed">
            Kept in this browser tab only (session storage) and sent with each question. Grepwise
            uses it for that request and never logs or stores it. Get one free at
            aistudio.google.com.
          </p>
        </form>
        <DialogFooter>
          {saved && (
            <Button
              variant="outline"
              onClick={() => {
                setOwnKey(null);
                setDraft("");
                setSaved(false);
              }}
            >
              Remove key
            </Button>
          )}
          <Button onClick={save}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
