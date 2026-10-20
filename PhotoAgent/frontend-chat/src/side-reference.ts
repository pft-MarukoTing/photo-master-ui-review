export type SideQuestion = { id: string; source_revision: number; photo_identity: string; original_text?: string;
  choices: { value: "subject" | "image"; label: string }[] };
export type SideAnswer = { answer_to: string; source_revision: number; selected_frame?: "subject" | "image"; token?: string };

export function sideAnswer(question: SideQuestion, frame: string): SideAnswer {
  if ((frame !== "subject" && frame !== "image") || !question.choices.some(c => c.value === frame)) throw new Error("無效的左右參考系選項");
  return { answer_to: question.id, selected_frame: frame, source_revision: question.source_revision };
}

export function sideChoice(question: SideQuestion, label: string): SideAnswer {
  const selected = question.choices.find(c => c.label === label);
  return selected ? sideAnswer(question, selected.value) : { answer_to: question.id, source_revision: question.source_revision };
}
