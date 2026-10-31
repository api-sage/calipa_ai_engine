// Words for the API's codes. The codes stay visible beside them (in mono), so an
// auditor can match the UI to the report and the trail.

export const OBJECTIVES: Record<string, { label: string; what: string }> = {
  transient_account_reconciliation: {
    label: "Transient account reconciliation",
    what: "Every transfer clears the FCY transient account to zero, with the legs it should have.",
  },
  fee_vat_leg_completeness: {
    label: "Fee and VAT legs",
    what: "Each transfer carries its charge and VAT legs, at the tariff's amounts.",
  },
  fx_conversion_check: {
    label: "FX conversion",
    what: "Conversions use the day's recorded rate, rounded the way the tariff says.",
  },
  duplicate_detection: {
    label: "Duplicates",
    what: "No entry or instruction appears twice.",
  },
  transaction_trace: {
    label: "Transaction trace",
    what: "Follow named transfers through every leg they posted.",
  },
};

export const objectiveLabel = (o: string) => OBJECTIVES[o]?.label ?? o.replace(/_/g, " ");

export const ACCOUNT_ROLES: Record<string, string> = {
  TRANSIENT: "Transient",
  FEE_INCOME: "Fee income",
  VAT: "VAT",
};

export const EXCEPTION_TYPES: Record<string, string> = {
  STRUCTURE_INCOMPLETE: "A leg is missing",
  FEE_LEG_MISSING: "No charge leg",
  VAT_LEG_MISSING: "No VAT leg",
  FEE_AMOUNT_MISMATCH: "Charge amount is off",
  VAT_AMOUNT_MISMATCH: "VAT amount is off",
  FX_CONVERSION_MISMATCH: "Conversion is off",
  ROUNDING_DIFFERENCE: "Rounding difference",
  TRANSIENT_RESIDUAL: "Transient does not clear",
  FEE_VAT_IN_BOTH_CURRENCIES: "Fee or VAT in both currencies",
  ENTRY_DIRECTION_INCONSISTENT: "Debit and credit disagree",
  DUPLICATE_ENTRY: "Duplicate entry",
  DUPLICATE_INSTRUCTION_CANDIDATE: "Possible duplicate instruction",
  RATE_UNAVAILABLE: "No rate for the day",
  RATE_AMBIGUOUS: "More than one rate for the day",
};
