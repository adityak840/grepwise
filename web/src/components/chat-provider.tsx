"use client";

import { createContext, type ReactNode, useContext } from "react";

import { useChat } from "@/hooks/use-chat";

type Chat = ReturnType<typeof useChat>;

const ChatContext = createContext<Chat | null>(null);

/**
 * Holds the conversation above the pages, so switching tabs (Ask -> Sources -> Ask) keeps it,
 * and an answer that is still streaming keeps streaming while you look at another tab.
 */
export function ChatProvider({ children }: { children: ReactNode }) {
  const chat = useChat();
  return <ChatContext.Provider value={chat}>{children}</ChatContext.Provider>;
}

export function useChatContext(): Chat {
  const chat = useContext(ChatContext);
  if (!chat) throw new Error("useChatContext must be used inside <ChatProvider>");
  return chat;
}
