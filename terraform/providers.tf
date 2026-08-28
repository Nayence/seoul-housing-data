provider "aws" {
  region = var.aws_region

  # Ces tags sont appliques automatiquement a TOUTE ressource creee par cette
  # configuration. Interet concret : dans Cost Explorer, on peut filtrer la
  # facture sur Project=seoul-housing et savoir exactement ce que coute ce
  # projet, sans confusion avec le reste du compte.
  #
  # ManagedBy=terraform sert d'avertissement : une ressource portant ce tag
  # ne doit jamais etre modifiee a la main dans la console.
  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
