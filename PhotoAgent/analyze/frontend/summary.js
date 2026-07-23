(function exposeAnalyzeSummary(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AnalyzeSummary = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  function finiteNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function mean(values) {
    if (!values.length) return null;
    return values.reduce((sum, value) => sum + value, 0) / values.length;
  }

  function calculateSummary(results, dimensionKeys) {
    const rows = Array.isArray(results) ? results : [];
    const keys = Array.isArray(dimensionKeys) ? dimensionKeys : [];
    const scored = rows.filter(
      (result) => result?.status === "scored" && finiteNumber(result.final_score) !== null,
    );
    const finalScores = scored.map((result) => finiteNumber(result.final_score));
    // The original photo's overall averages, so the Summary can show
    // original-average vs edited-average side by side (like the detail view).
    const originalScores = scored
      .map((result) => finiteNumber(result.original?.final_score))
      .filter((value) => value !== null);

    const dimensions = {};
    keys.forEach((key) => {
      const samples = scored
        .map((result) => result.dimensions?.[key])
        .filter((dimension) => dimension?.status === "scored" && finiteNumber(dimension.score) !== null);
      const scores = samples.map((dimension) => finiteNumber(dimension.score));
      const weighted = samples.find((dimension) => finiteNumber(dimension.weight) !== null);
      const originalDimScores = scored
        .map((result) => result.original?.dimensions?.[key])
        .filter((dimension) => dimension?.status === "scored" && finiteNumber(dimension.score) !== null)
        .map((dimension) => finiteNumber(dimension.score));
      dimensions[key] = {
        average: mean(scores),
        originalAverage: mean(originalDimScores),
        count: scores.length,
        weight: weighted ? finiteNumber(weighted.weight) : null,
      };
    });

    return {
      total: rows.length,
      scored: scored.length,
      failed: rows.filter((result) => result?.status === "error").length,
      invalid: rows.filter((result) => result?.status === "invalid").length,
      average: mean(finalScores),
      originalAverage: mean(originalScores),
      highest: finalScores.length ? Math.max(...finalScores) : null,
      lowest: finalScores.length ? Math.min(...finalScores) : null,
      dimensions,
    };
  }

  return { calculateSummary };
});
