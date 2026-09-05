export type QueuedImage = { data: string; mime: string };

type UserMessageSender = (content: unknown, options?: { deliverAs: "steer" }) => Promise<void>;

const IMAGE_MARKER = "[Image attached]";

export async function sendTelegramDelivery(
  sendUserMessage: UserMessageSender,
  idle: boolean,
  text: string,
  image?: QueuedImage,
): Promise<void> {
  const options = idle ? undefined : { deliverAs: "steer" as const };
  if (image) {
    const content = [
      { type: "text", text: text.trim() ? `${text}\n\n${IMAGE_MARKER}` : IMAGE_MARKER },
      { type: "image", data: image.data, mimeType: image.mime },
    ];
    await sendUserMessage(content, options);
  } else {
    await sendUserMessage(text, options);
  }
}
