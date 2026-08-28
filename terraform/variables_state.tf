variable "state_bucket_name" {
  description = "Bucket contenant l'etat Terraform. Nom unique au monde."
  type        = string
  default     = "seoul-housing-tfstate-anice"
}
