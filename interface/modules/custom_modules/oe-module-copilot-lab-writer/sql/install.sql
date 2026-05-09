CREATE TABLE IF NOT EXISTS `copilot_lab_result_map` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `patient_id` bigint(20) NOT NULL,
  `source_document_reference` varchar(128) NOT NULL,
  `field_path` varchar(255) NOT NULL,
  `procedure_order_id` bigint(20) NOT NULL,
  `procedure_report_id` bigint(20) NOT NULL,
  `procedure_result_id` bigint(20) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `copilot_lab_result_natural_key` (`patient_id`, `source_document_reference`, `field_path`),
  KEY `procedure_result_id` (`procedure_result_id`)
) ENGINE=InnoDB;
