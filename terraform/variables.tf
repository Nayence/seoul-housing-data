# Variables du projet.
#
# Regle appliquee ici : tout ce qui pourrait changer d'un environnement a
# l'autre est une variable. Tout ce qui est structurel reste en dur.

variable "aws_region" {
  description = "Region AWS. Seoul : proche des sources de donnees et de l'auteur."
  type        = string
  default     = "ap-northeast-2"
}

variable "project_name" {
  description = "Prefixe des ressources et valeur du tag Project."
  type        = string
  default     = "seoul-housing"
}

variable "environment" {
  description = "Environnement de deploiement."
  type        = string
  default     = "prod"

  # Une validation echoue au moment du plan, avec un message clair,
  # plutot que de creer des ressources mal nommees.
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment doit valoir dev ou prod."
  }
}

variable "raw_bucket_name" {
  description = "Bucket de la couche brute. Le nom S3 est unique au monde."
  type        = string
  default     = "seoul-housing-raw-anice"
}

variable "noncurrent_transition_days" {
  description = "Jours avant bascule des anciennes versions en stockage froid."
  type        = number
  default     = 30
}

variable "noncurrent_expiration_days" {
  description = "Jours avant suppression definitive des anciennes versions."
  type        = number
  default     = 365
}
