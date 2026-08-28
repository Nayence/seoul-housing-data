variable "api_key_parameter" {
  description = "Chemin SSM du parametre contenant la cle data.go.kr."
  type        = string
  default     = "/seoul-housing/prod/data-go-kr-key"
}

variable "lambda_timeout" {
  description = "Timeout de la Lambda de collecte, en secondes."
  type        = number
  default     = 120

  # Une unite de travail vaut ~4 appels API, soit une dizaine de secondes.
  # 120 s laisse de la marge pour les reessais sans risquer de bloquer
  # longtemps une invocation defaillante.
  validation {
    condition     = var.lambda_timeout >= 30 && var.lambda_timeout <= 900
    error_message = "lambda_timeout doit etre entre 30 et 900 secondes."
  }
}

variable "collector_max_concurrency" {
  description = "Invocations simultanees maximum de la Lambda de collecte."
  type        = number
  default     = 2

  # C'est le bridage du debit vers data.go.kr. 2 invocations = ~8 appels
  # simultanes. Augmenter avec prudence : le quota est de 10 000 par jour
  # et le serveur est un service public.
  validation {
    condition     = var.collector_max_concurrency >= 2 && var.collector_max_concurrency <= 10
    error_message = "collector_max_concurrency doit etre entre 2 et 10."
  }
}

variable "lookback_months" {
  description = "Nombre de mois reingeres a chaque execution mensuelle."
  type        = number
  default     = 3
}

variable "log_retention_days" {
  description = "Retention des logs CloudWatch, en jours."
  type        = number
  default     = 14
}
