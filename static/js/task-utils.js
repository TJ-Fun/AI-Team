export function getAskedProvider(task) {
  if (task.provider) return task.provider;
  if (task.assigned_role === "OpenRouter free agent") return "OpenRouter";
  if (task.assigned_role === "Gemini") return "Gemini";
  return "Automatic";
}
